"""OpenTelemetry export: a reply's runs as OTLP JSON, written by YAAP itself.

YAAP has no OpenTelemetry SDK as a dependency, to keep its supply chain small: the OTLP/JSON document is built
here from the run records and their steps. It follows the OTLP/HTTP JSON encoding (`resourceSpans[].scopeSpans[]
.spans[]`, ids as hex, times as nanoseconds and 64-bit integers as strings, enums as numbers) and the GenAI semantic
conventions of OpenTelemetry semantic conventions v1.37.0 (status Development):

- one trace per reply: its top-level run is an `invoke_agent` span, a sub-run an `invoke_agent` span under the
  `delegate` tool call that started it (or under its parent run when no such call is found), at any depth;
- a model call is a `chat {model}` span (kind CLIENT) with `gen_ai.provider.name` (and the older `gen_ai.system`
  for collectors that still read it), `gen_ai.request.model`, `gen_ai.response.model` and
  `gen_ai.usage.input_tokens`/`output_tokens`; cache tokens, cost and the call count are `yaap.*` attributes;
- a tool call is an `execute_tool {tool}` span with `gen_ai.tool.name`, its approval decision and its error;
- start times and durations are the steps' own; trace and span ids are derived from the run and step ids, so an
  export made twice names the same spans.

Prompt content is left out unless the caller passes a snapshot loader (the `otlp_include_content` setting): then a
model call carries `gen_ai.system_instructions` and `gen_ai.input.messages` from its context snapshot (where secrets
are only placeholders, as stored), a tool call `gen_ai.tool.call.arguments` (its stored, masked summary) and a sub-run
its task.
"""

import hashlib
import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.storage.models import AgentRunRecord, Conversation, RunStep
from app.tracing.snapshots import JsonValue, StoredSnapshot
from app.version import VERSION

SEMCONV_VERSION = "1.37.0"
SCHEMA_URL = f"https://opentelemetry.io/schemas/{SEMCONV_VERSION}"
SCOPE_NAME = "yaap.tracing"
SERVICE_NAME = "yaap"
# YAAP's version, as the app and its MCP server and client name it.
SERVICE_VERSION = VERSION
SPAN_KIND_INTERNAL = 1
SPAN_KIND_CLIENT = 3
STATUS_ERROR = 2
# Run statuses that end a run in error (complete and incomplete do not).
ERROR_STATUSES = frozenset({"error", "refused"})
# Tools whose call starts a sub-run: the sub-run's span hangs under the call.
DELEGATION_TOOLS = frozenset({"delegate"})
# The most replies one period export holds; a longer period is refused so a download stays bounded.
MAX_TRACES = 1000
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

AttributeValue = str | int | float | bool
OtlpJson = dict[str, object]
# The context snapshot of a model call by its digest; None when it is gone.
SnapshotLoader = Callable[[str], StoredSnapshot | None]
IdKind = Literal["trace", "run", "step"]


@dataclass(frozen=True)
class TraceRun:
    """A run record with its steps in order and its sub-runs."""

    record: AgentRunRecord
    steps: tuple[RunStep, ...]
    children: tuple["TraceRun", ...] = ()


@dataclass(frozen=True)
class ReplyTrace:
    """One reply's runs, with what the export says about the conversation."""

    root: TraceRun
    conversation_id: int
    environment_id: int
    project_id: int | None
    # The conversation's provider: the provider of a model the catalog does not know.
    provider: str


@dataclass(frozen=True)
class ExportOptions:
    # The provider of a model by its id, from the model catalog; None when unknown.
    provider_of: Callable[[str], str | None]
    # Given, prompt content is exported from the context snapshots; None leaves it out.
    content: SnapshotLoader | None = None


@dataclass
class SpanContext:
    trace_id: str
    trace: ReplyTrace
    options: ExportOptions
    spans: list[OtlpJson] = field(default_factory=list)


# Ids and values.


def hex_id(kind: IdKind, value: int, length: int) -> str:
    """A stable id of length hex digits for a run or step; never all zeros, which OTLP reads as no id."""
    digest = hashlib.sha256(f"yaap:{kind}:{value}".encode()).hexdigest()[:length]
    return digest if digest.strip("0") else "1".rjust(length, "0")


def trace_id(root_run_id: int) -> str:
    return hex_id("trace", root_run_id, 32)


def run_span_id(run_id: int) -> str:
    return hex_id("run", run_id, 16)


def step_span_id(step_id: int) -> str:
    return hex_id("step", step_id, 16)


def nanos(moment: datetime) -> str:
    """Nanoseconds since the epoch as a string (a fixed64 in OTLP JSON); a time without zone is UTC."""
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return str((aware - EPOCH) // timedelta(microseconds=1) * 1000)


def any_value(value: AttributeValue) -> OtlpJson:
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    return {"stringValue": value}


def attributes(values: dict[str, AttributeValue | None]) -> list[OtlpJson]:
    """OTLP key-value pairs; empty values are left out."""
    return [{"key": key, "value": any_value(value)} for key, value in values.items() if value is not None]


def compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


# Prompt content in the shape of the GenAI conventions.


def message_parts(blocks: Iterable[JsonValue]) -> tuple[list[JsonValue], list[JsonValue]]:
    """A neutral message's blocks as GenAI parts, and the tool results it holds as tool_call_response parts."""
    parts: list[JsonValue] = []
    responses: list[JsonValue] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            parts.append({"type": "text", "content": str(block.get("text") or "")})
        elif kind == "tool_call":
            parts.append(
                {"type": "tool_call", "id": block.get("id"), "name": block.get("name"), "arguments": block.get("input")}
            )
            if block.get("output") is not None:
                responses.append({"type": "tool_call_response", "id": block.get("id"), "response": block.get("output")})
        else:
            parts.append({"type": str(kind or "unknown")})
    return parts, responses


def input_messages(messages: Sequence[JsonValue]) -> list[JsonValue]:
    """A snapshot's messages as `gen_ai.input.messages`: role and parts, tool results as messages of role tool."""
    converted: list[JsonValue] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        parts, responses = message_parts(content if isinstance(content, list) else [])
        if parts:
            converted.append({"role": str(message.get("role") or "user"), "parts": parts})
        if responses:
            converted.append({"role": "tool", "parts": responses})
    return converted


def snapshot_attributes(snapshot: StoredSnapshot) -> dict[str, AttributeValue | None]:
    instructions = [{"type": "text", "content": part.text} for part in snapshot.system]
    return {
        "gen_ai.system_instructions": compact_json(instructions),
        "gen_ai.input.messages": compact_json(input_messages(snapshot.messages)),
        "yaap.snapshot.complete": snapshot.complete,
    }


# Spans.


def span(
    context: SpanContext,
    span_id: str,
    parent_id: str | None,
    name: str,
    kind: int,
    start: datetime,
    end: datetime,
    values: dict[str, AttributeValue | None],
    error: str | None = None,
) -> OtlpJson:
    item: OtlpJson = {
        "traceId": context.trace_id,
        "spanId": span_id,
        "parentSpanId": parent_id or "",
        "name": name,
        "kind": kind,
        "startTimeUnixNano": nanos(start),
        "endTimeUnixNano": nanos(max(end, start)),
        "attributes": attributes(values),
        "status": {"code": STATUS_ERROR, "message": error} if error else {},
    }
    context.spans.append(item)
    return item


def step_end(step: RunStep) -> datetime:
    return step.started_at + timedelta(milliseconds=max(step.duration_ms, 0))


def run_end(run: TraceRun) -> datetime:
    """When a run ended; for one still running, the end of its last step."""
    if run.record.finished_at is not None:
        return run.record.finished_at
    ends = [step_end(step) for step in run.steps] + [run_end(child) for child in run.children]
    return max([run.record.started_at, *ends])


def model_span(context: SpanContext, step: RunStep, parent: str) -> None:
    model = step.model or ""
    provider = context.options.provider_of(model) or context.trace.provider
    values: dict[str, AttributeValue | None] = {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": provider,
        "gen_ai.system": provider,
        "gen_ai.request.model": model,
        "gen_ai.response.model": model,
        "gen_ai.conversation.id": str(context.trace.conversation_id),
        "gen_ai.usage.input_tokens": step.input_tokens,
        "gen_ai.usage.output_tokens": step.output_tokens,
        "yaap.usage.cache_read_tokens": step.cache_read_tokens,
        "yaap.usage.cache_write_tokens": step.cache_write_tokens,
        "yaap.cost_usd": step.cost,
        "yaap.model_calls": step.model_calls,
    }
    loader = context.options.content
    stored = loader(step.snapshot) if loader is not None and step.snapshot else None
    if stored is not None:
        values.update(snapshot_attributes(stored))
    name = f"chat {model}".strip()
    span(context, step_span_id(step.id), parent, name, SPAN_KIND_CLIENT, step.started_at, step_end(step), values)


def tool_span(context: SpanContext, step: RunStep, parent: str) -> None:
    tool = step.tool or ""
    values: dict[str, AttributeValue | None] = {
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name": tool,
        "gen_ai.tool.type": "function",
        "gen_ai.conversation.id": str(context.trace.conversation_id),
        "yaap.tool.approval": step.approval,
        "yaap.tool.output_ref": step.output_ref,
        "error.type": "tool_error" if step.is_error else None,
    }
    if context.options.content is not None:
        values["gen_ai.tool.call.arguments"] = step.summary
    error = "The tool call failed." if step.is_error else None
    name = f"execute_tool {tool}".strip()
    span(
        context, step_span_id(step.id), parent, name, SPAN_KIND_INTERNAL, step.started_at, step_end(step), values, error
    )


def delegating_step(run: TraceRun, child: TraceRun) -> RunStep | None:
    """The delegate call of run during which child started: the latest such call that started before it."""
    started = child.record.started_at
    calls = [
        step
        for step in run.steps
        if step.kind == "tool" and step.tool in DELEGATION_TOOLS and step.started_at <= started <= step_end(step)
    ]
    return max(calls, key=lambda step: (step.started_at, step.id)) if calls else None


def run_spans(context: SpanContext, run: TraceRun, parent: str | None) -> None:
    trace = context.trace
    agent = run.record.agent or ("YAAP" if run.record.parent_run_id is None else "sub-run")
    model = run.record.model
    provider = context.options.provider_of(model) or trace.provider
    values: dict[str, AttributeValue | None] = {
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.agent.name": agent,
        "gen_ai.provider.name": provider,
        "gen_ai.system": provider,
        "gen_ai.request.model": model,
        "gen_ai.conversation.id": str(trace.conversation_id),
        "yaap.run.id": run.record.id,
        "yaap.run.status": run.record.status,
        "yaap.run.tool_calls": run.record.tool_calls,
        "yaap.run.worker": run.record.worker,
        "yaap.environment.id": trace.environment_id,
        "yaap.project.id": trace.project_id,
        "yaap.message.id": run.record.message_id,
    }
    if context.options.content is not None:
        values["yaap.run.task"] = run.record.task
    error = f"The run ended as {run.record.status}." if run.record.status in ERROR_STATUSES else None
    own = run_span_id(run.record.id)
    start, end = run.record.started_at, run_end(run)
    span(context, own, parent, f"invoke_agent {agent}", SPAN_KIND_INTERNAL, start, end, values, error)
    for step in run.steps:
        (model_span if step.kind == "model" else tool_span)(context, step, own)
    for child in run.children:
        call = delegating_step(run, child)
        run_spans(context, child, step_span_id(call.id) if call is not None else own)


def reply_spans(trace: ReplyTrace, options: ExportOptions) -> list[OtlpJson]:
    """The spans of one reply: one trace, its id derived from the top-level run."""
    context = SpanContext(trace_id(trace.root.record.id), trace, options)
    run_spans(context, trace.root, None)
    return context.spans


def otlp_document(spans: list[OtlpJson]) -> OtlpJson:
    """An OTLP/JSON trace export (ExportTraceServiceRequest) holding spans."""
    resource = attributes({"service.name": SERVICE_NAME, "service.version": SERVICE_VERSION})
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": resource},
                "scopeSpans": [
                    {"scope": {"name": SCOPE_NAME, "version": SERVICE_VERSION}, "spans": spans, "schemaUrl": SCHEMA_URL}
                ],
                "schemaUrl": SCHEMA_URL,
            }
        ]
    }


# Reading what is exported. A run is exported only while its conversation exists in the run's environment, as the
# conversation APIs show it; the period export reads only such runs.


def visible(record: AgentRunRecord, conversation: Conversation | None) -> bool:
    return conversation is not None and conversation.environment_id == record.environment_id


def run_tree(record: AgentRunRecord, records: Sequence[AgentRunRecord], steps: dict[int, list[RunStep]]) -> TraceRun:
    """record with its sub-runs at any depth, from the conversation's records."""
    by_parent: dict[int, list[AgentRunRecord]] = {}
    for item in records:
        if item.parent_run_id is not None:
            by_parent.setdefault(item.parent_run_id, []).append(item)

    def build(node: AgentRunRecord, seen: frozenset[int]) -> TraceRun:
        children = [child for child in by_parent.get(node.id, []) if child.id not in seen]
        return TraceRun(
            node,
            tuple(steps.get(node.id, [])),
            tuple(build(child, seen | {child.id}) for child in sorted(children, key=lambda item: item.id)),
        )

    return build(record, frozenset({record.id}))


def steps_of(session: Session, run_ids: Sequence[int]) -> dict[int, list[RunStep]]:
    steps: dict[int, list[RunStep]] = {}
    for start in range(0, len(run_ids), 500):
        chunk = run_ids[start : start + 500]
        rows = session.scalars(
            select(RunStep)
            .where(RunStep.run_id.in_(chunk), RunStep.kind != "note")
            .order_by(RunStep.run_id, RunStep.position, RunStep.id)
        )
        for row in rows:
            steps.setdefault(row.run_id, []).append(row)
    return steps


def conversation_traces(
    session: Session, conversation: Conversation, roots: Sequence[AgentRunRecord]
) -> list[ReplyTrace]:
    query = select(AgentRunRecord).where(AgentRunRecord.conversation_id == conversation.id)
    records = list(session.scalars(query.order_by(AgentRunRecord.id)))
    steps = steps_of(session, [record.id for record in records])
    return [
        ReplyTrace(
            run_tree(root, records, steps),
            conversation.id,
            conversation.environment_id,
            conversation.project_id,
            conversation.provider,
        )
        for root in roots
    ]


def top_level(session: Session, record: AgentRunRecord) -> AgentRunRecord:
    """The reply's run a run belongs to: its top-level ancestor."""
    seen = {record.id}
    while record.parent_run_id is not None and record.parent_run_id not in seen:
        parent = session.get(AgentRunRecord, record.parent_run_id)
        if parent is None:
            break
        seen.add(parent.id)
        record = parent
    return record


def reply_trace(session: Session, run_id: int) -> ReplyTrace | None:
    """The trace of the reply a run belongs to (a sub-run gives its whole reply); None when it cannot be seen."""
    record = session.get(AgentRunRecord, run_id)
    if record is None:
        return None
    root = top_level(session, record)
    conversation = session.get(Conversation, root.conversation_id)
    if conversation is None or not visible(root, conversation):
        return None
    return conversation_traces(session, conversation, [root])[0]


class TooManyTraces(Exception):
    """The period holds more replies than one export may."""


def period_traces(
    session: Session, since: datetime, until: datetime, environment_id: int | None = None
) -> list[ReplyTrace]:
    """The replies that started in [since, until), oldest first, in one environment or all."""
    query = (
        select(AgentRunRecord, Conversation)
        .join(Conversation, Conversation.id == AgentRunRecord.conversation_id)
        .where(
            AgentRunRecord.parent_run_id.is_(None),
            AgentRunRecord.started_at >= since,
            AgentRunRecord.started_at < until,
            Conversation.environment_id == AgentRunRecord.environment_id,
        )
        .order_by(AgentRunRecord.started_at, AgentRunRecord.id)
        .limit(MAX_TRACES + 1)
    )
    if environment_id is not None:
        query = query.where(AgentRunRecord.environment_id == environment_id)
    rows = session.execute(query).all()
    if len(rows) > MAX_TRACES:
        raise TooManyTraces(f"The period holds more than {MAX_TRACES} replies; choose a shorter period.")
    by_conversation: dict[int, tuple[Conversation, list[AgentRunRecord]]] = {}
    for record, conversation in rows:
        by_conversation.setdefault(conversation.id, (conversation, []))[1].append(record)
    traces = [
        trace
        for conversation, roots in by_conversation.values()
        for trace in conversation_traces(session, conversation, roots)
    ]
    return sorted(traces, key=lambda trace: (trace.root.record.started_at, trace.root.record.id))
