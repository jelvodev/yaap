"""Where the tokens of a conversation went, over the whole session: per reply, its workers, its utility calls, the
largest tool results and the cache hit rate.

What counts once. A reply's `UsageRecord` (purpose "main") is exactly the sum of the model calls of the reply's own
run (app/tracing/provider.py): it does not include its sub-runs. Each sub-run (a worker, at any depth) has its own run
with its own model steps, and also a `UsageRecord` with purpose "worker" and no message. So workers are read from
their run steps and the "worker" usage records are not read at all; a worker's tokens are in one row only, and the
reply row never holds them. Utility calls (purpose "utility") are separate records; those with a message of a reply
sit under that reply, the others are `other_utility`.

Cost. A row's cost is None when it is not paid per token (a subscription). Totals add the costs that exist and stay
None only when no row has one.

Thinking has no stored count: it is inside `output_tokens`.
"""

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.outputs import OutputStore
from app.core.token_hints import ReplyGap
from app.core.utility_tasks import task_label
from app.storage.models import UsageRecord
from app.tracing.division import estimate_tokens
from app.tracing.steps import RunNode, StepView, conversation_runs, steps_cost, steps_of_runs, walk

TokenRowKind = Literal["reply", "worker", "utility", "session"]

TOP_TOOL_RESULTS = 10
WORKER_LABEL_CHARS = 60
SESSION_LABEL = "Session"
READ_TOOL = "read_file"
PATH_IN_SUMMARY = re.compile(r'"path"\s*:\s*"((?:[^"\\]|\\.)*)')


@dataclass(frozen=True)
class TokenRow:
    kind: TokenRowKind
    # "Reply" for replies; a worker's task (shortened) or worker name; a utility task's label.
    label: str
    message_id: int | None
    run_id: int | None
    started_at: datetime | None
    cache_read_tokens: int
    cache_write_tokens: int
    # Uncached input.
    input_tokens: int
    # Includes thinking: no separate thinking count is stored.
    output_tokens: int
    # None when not paid per token (subscription).
    cost: float | None


@dataclass(frozen=True)
class ReplyTokens:
    reply: TokenRow
    workers: list[TokenRow]
    utility: list[TokenRow]


@dataclass(frozen=True)
class ToolResultTokens:
    step_id: int
    run_id: int
    message_id: int | None
    tool: str
    summary: str | None
    # Estimated from the size of the stored output: characters / 4, rounded up (bytes stand in for characters).
    tokens: int


@dataclass(frozen=True)
class SessionTokens:
    # Oldest first.
    replies: list[ReplyTokens]
    # Utility calls of the conversation that belong to no reply.
    other_utility: list[TokenRow]
    # The ten largest, largest first.
    top_tool_results: list[ToolResultTokens]
    # Cache read / (input + cache read + cache write) over replies and workers; None without prompt tokens.
    cache_hit_rate: float | None
    totals: TokenRow
    # Path -> how many times read_file read it, for the hints.
    file_reads: dict[str, int]
    # Pause before each reply after the first, with its cache use, for the hints.
    gaps: list[ReplyGap]


@dataclass(frozen=True)
class UsageFacts:
    """One stored usage record, as far as the view needs it."""

    message_id: int | None
    task: str | None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost: float | None
    created_at: datetime


@dataclass(frozen=True)
class ReplyInput:
    """A reply: its message, its top-level run when it had one, and its main usage record when it was stored."""

    message_id: int
    run: RunNode | None
    main: UsageFacts | None


def add_costs(costs: Iterable[float | None]) -> float | None:
    present = [cost for cost in costs if cost is not None]
    return sum(present) if present else None


def reply_row(item: ReplyInput, steps: Mapping[int, list[StepView]]) -> TokenRow:
    """The reply's own tokens: its main usage record, or else what its run's model calls add up to."""
    run_id = item.run.id if item.run is not None else None
    started_at = item.run.started_at if item.run is not None else (item.main.created_at if item.main else None)
    if item.main is not None:
        main = item.main
        return TokenRow(
            "reply", "Reply", item.message_id, run_id, started_at,
            main.cache_read_tokens, main.cache_write_tokens, main.input_tokens, main.output_tokens, main.cost,
        )  # fmt: skip
    return run_row("reply", "Reply", item.message_id, item.run, steps.get(run_id, []) if run_id is not None else [])


def run_row(
    kind: TokenRowKind, label: str, message_id: int | None, run: RunNode | None, steps: list[StepView]
) -> TokenRow:
    models = [step for step in steps if step.kind == "model"]
    return TokenRow(
        kind,
        label,
        message_id,
        run.id if run is not None else None,
        run.started_at if run is not None else None,
        sum(step.usage.cache_read_tokens for step in models),
        sum(step.usage.cache_write_tokens for step in models),
        sum(step.usage.input_tokens for step in models),
        sum(step.usage.output_tokens for step in models),
        steps_cost(models),
    )


def worker_label(run: RunNode) -> str:
    text = " ".join((run.task or "").split())
    if not text:
        return run.worker or run.agent or "Worker"
    return text if len(text) <= WORKER_LABEL_CHARS else text[: WORKER_LABEL_CHARS - 1] + "…"


def worker_rows(root: RunNode, message_id: int, steps: Mapping[int, list[StepView]]) -> list[TokenRow]:
    """One row per sub-run of the reply, at any depth, each before its own sub-runs."""
    return [
        run_row("worker", worker_label(run), message_id, run, steps.get(run.id, []))
        for run in walk(root.children)
    ]


def utility_row(record: UsageFacts, message_id: int | None) -> TokenRow:
    return TokenRow(
        "utility",
        task_label(record.task),
        message_id,
        None,
        record.created_at,
        record.cache_read_tokens,
        record.cache_write_tokens,
        record.input_tokens,
        record.output_tokens,
        record.cost,
    )


def reply_rows(
    inputs: list[ReplyInput],
    utility: Mapping[int, list[UsageFacts]],
    steps: Mapping[int, list[StepView]],
) -> list[ReplyTokens]:
    """The replies oldest first, each with its worker rows and the utility calls that name its message."""
    return [
        ReplyTokens(
            reply=reply_row(item, steps),
            workers=worker_rows(item.run, item.message_id, steps) if item.run is not None else [],
            utility=[utility_row(record, item.message_id) for record in utility.get(item.message_id, [])],
        )
        for item in sorted(inputs, key=lambda item: item.message_id)
    ]


def hit_rate(rows: Iterable[TokenRow]) -> float | None:
    rows = list(rows)
    read = sum(row.cache_read_tokens for row in rows)
    prompt = read + sum(row.cache_write_tokens + row.input_tokens for row in rows)
    return read / prompt if prompt > 0 else None


def top_results(results: Iterable[ToolResultTokens], limit: int = TOP_TOOL_RESULTS) -> list[ToolResultTokens]:
    """The largest results first; equal sizes keep the order they happened in."""
    return sorted(results, key=lambda result: (-result.tokens, result.step_id))[:limit]


def total_row(rows: Iterable[TokenRow]) -> TokenRow:
    rows = list(rows)
    return TokenRow(
        "session",
        SESSION_LABEL,
        None,
        None,
        None,
        sum(row.cache_read_tokens for row in rows),
        sum(row.cache_write_tokens for row in rows),
        sum(row.input_tokens for row in rows),
        sum(row.output_tokens for row in rows),
        add_costs(row.cost for row in rows),
    )


def read_path(summary: str | None) -> str | None:
    """The path of a read_file step. Its summary is the arguments as compact JSON, cut short when long, so a cut
    summary is read by pattern."""
    if not summary:
        return None
    try:
        arguments = json.loads(summary)
    except ValueError:
        arguments = None
    if isinstance(arguments, dict) and isinstance(arguments.get("path"), str):
        return arguments["path"]
    found = PATH_IN_SUMMARY.search(summary)
    return found.group(1) if found else None


def file_reads(steps: Iterable[StepView]) -> dict[str, int]:
    reads: dict[str, int] = {}
    for step in steps:
        path = read_path(step.summary) if step.kind == "tool" and step.tool == READ_TOOL else None
        if path:
            reads[path] = reads.get(path, 0) + 1
    return reads


@dataclass(frozen=True)
class ReplySpan:
    started_at: datetime | None
    finished_at: datetime | None
    cache_read_tokens: int
    cache_write_tokens: int


def reply_gaps(spans: list[ReplySpan]) -> list[ReplyGap]:
    """For each reply after the first: the pause between the previous reply's end and its start, and its cache use. A
    reply without times, or after one without an end, has no known pause and is skipped."""
    gaps: list[ReplyGap] = []
    for before, after in zip(spans, spans[1:], strict=False):
        if before.finished_at is None or after.started_at is None:
            continue
        pause = max((after.started_at - before.finished_at).total_seconds(), 0.0)
        gaps.append(ReplyGap(pause, after.cache_read_tokens, after.cache_write_tokens))
    return gaps


def assemble(
    replies: list[ReplyTokens],
    other_utility: list[TokenRow],
    results: list[ToolResultTokens],
    reads: dict[str, int],
    spans: list[ReplySpan],
) -> SessionTokens:
    billed = [row for item in replies for row in (item.reply, *item.workers)]
    every = [*billed, *(row for item in replies for row in item.utility), *other_utility]
    return SessionTokens(
        replies=replies,
        other_utility=other_utility,
        top_tool_results=top_results(results),
        cache_hit_rate=hit_rate(billed),
        totals=total_row(every),
        file_reads=reads,
        gaps=reply_gaps(spans),
    )


def usage_facts(record: UsageRecord) -> UsageFacts:
    return UsageFacts(
        record.message_id,
        record.task,
        record.input_tokens,
        record.output_tokens,
        record.cache_read_tokens,
        record.cache_write_tokens,
        record.cost,
        record.created_at,
    )


def merged_main(records: Iterable[UsageFacts]) -> dict[int, UsageFacts]:
    """The main usage of each reply. A reply that stored more than one record (a retry, a continued run) used the
    tokens of all of them."""
    merged: dict[int, UsageFacts] = {}
    for record in records:
        if record.message_id is None:
            continue
        before = merged.get(record.message_id)
        if before is None:
            merged[record.message_id] = record
            continue
        merged[record.message_id] = UsageFacts(
            record.message_id,
            before.task,
            before.input_tokens + record.input_tokens,
            before.output_tokens + record.output_tokens,
            before.cache_read_tokens + record.cache_read_tokens,
            before.cache_write_tokens + record.cache_write_tokens,
            add_costs([before.cost, record.cost]),
            min(before.created_at, record.created_at),
        )
    return merged


def stored_size(outputs: OutputStore, number: int) -> int | None:
    """The size of a stored output in characters, estimated by its bytes on disk so that the text is not read."""
    path = outputs.path(number)
    return path.stat().st_size if path is not None else None


def stored_results(
    runs: Iterable[tuple[RunNode, int | None]], steps: Mapping[int, list[StepView]], outputs: OutputStore | None
) -> list[ToolResultTokens]:
    """Tool steps whose output was stored whole (long outputs only; shorter ones keep no size), with their size."""
    if outputs is None:
        return []
    found: list[ToolResultTokens] = []
    for run, message_id in runs:
        for step in steps.get(run.id, []):
            number = step.output_ref if step.kind == "tool" else None
            size = stored_size(outputs, number) if number is not None else None
            if size is not None:
                tokens = estimate_tokens(size)
                found.append(ToolResultTokens(step.id, run.id, message_id, step.tool or "", step.summary, tokens))
    return found


def session_tokens(session: Session, conversation_id: int, outputs: OutputStore | None = None) -> SessionTokens:
    """Where the tokens of a conversation went. `outputs` is the conversation's stored tool outputs, for the sizes of
    the largest tool results; without it the list is empty."""
    roots = conversation_runs(session, conversation_id)
    records = session.scalars(
        select(UsageRecord).where(UsageRecord.conversation_id == conversation_id).order_by(UsageRecord.id)
    ).all()
    main = merged_main(usage_facts(record) for record in records if record.purpose == "main")
    utility_records = [usage_facts(record) for record in records if record.purpose == "utility"]
    runs_by_message = {root.message_id: root for root in roots if root.message_id is not None}

    inputs = [
        ReplyInput(message_id, runs_by_message.get(message_id), main.get(message_id))
        for message_id in sorted({*main, *runs_by_message})
    ]
    reply_ids = {item.message_id for item in inputs}
    utility: dict[int, list[UsageFacts]] = {}
    unattached: list[TokenRow] = []
    for record in utility_records:
        if record.message_id in reply_ids:
            utility.setdefault(record.message_id, []).append(record)
        else:
            unattached.append(utility_row(record, None))

    owner = [(run, item.message_id) for item in inputs if item.run is not None for run in walk([item.run])]
    steps = steps_of_runs(session, (run.id for run, _ in owner))
    replies = reply_rows(inputs, utility, steps)
    spans = [
        ReplySpan(
            item.run.started_at if item.run else None,
            item.run.finished_at if item.run else None,
            row.reply.cache_read_tokens,
            row.reply.cache_write_tokens,
        )
        for item, row in zip(sorted(inputs, key=lambda item: item.message_id), replies, strict=True)
    ]
    reads = file_reads(step for run, _ in owner for step in steps.get(run.id, []))
    return assemble(replies, unattached, stored_results(owner, steps, outputs), reads, spans)
