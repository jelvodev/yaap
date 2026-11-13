"""Delegation: an agent hands a clear task to a sub-run, which reports back.

A sub-run is a short agent run of its own. It works on the same project with the same tools, permissions and approval
cards as the run that started it, on the worker model of the conversation's preset (or the model of a named agent).
It sees only its task, not the conversation. It cannot delegate itself, and the sub-runs of one run go one at a time.
Its approvals and progress appear in the conversation of the run that started it.

The worker can also be named: another preset, or Claude Code or Codex. A CLI worker is started through
its connection's adapter whatever connection the environment uses, so it has only YAAP's tools over MCP, bound by the
sub-run's token to the parent's project, target, tools and permissions (agent/workers.py).

In explore mode the sub-run only reads and searches, needs no approvals and reports back in at most
about 2,000 characters (agent/exploring.py).
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import anyio
from sqlalchemy.orm import Session, sessionmaker

from app.agent.brief import Brief, brief_message
from app.agent.brief_delegation import delegate_by_brief, delegate_unchecked
from app.agent.escalation import Role, sub_run_role
from app.agent.exploring import capped_report, explore_only
from app.agent.policy import AccessLevel
from app.agent.runs import AgentRun, RunRegistry
from app.agent.tool_catalog import with_listing
from app.agents.definitions import AgentDefinition, agent_part, find_agent
from app.core.audit import NO_PROJECT_DATA, ProjectData, record_request, sub_run_entry, with_refusal
from app.core.context_control import output_tokens
from app.core.model_choice import ForSubRun, ModelChoice, ModelChoiceError, PolicyForbids, read_key, resolve_provider
from app.core.outgoing_checks import OutgoingRules, checked_provider, outgoing_rules, request_refusal
from app.core.prompts import Languages
from app.core.stopped_usage import StoppedRun, stopped_usage
from app.credentials.crypto import CredentialCipher
from app.credentials.types import CredentialType
from app.blueprint.builtin import BuiltinName
from app.blueprint.builtin import instructions as builtin_instructions
from app.blueprint.context import build_system_parts
from app.blueprint.layers import Layer
from app.presets.delegation import DelegationMode
from app.presets.service import delegation_of
from app.providers.base import (
    AgentRequest,
    Completed,
    HistoryMessage,
    ModelProvider,
    ProviderError,
    SystemPart,
    TextDelta,
    ToolCallFinished,
    ToolCallStarted,
    ToolServer,
)
from app.providers.catalog import calculate_cost
from app.providers.registry import ProviderRegistry, ProviderSpec
from app.storage.models import AgentRunRecord, UsageRecord
from app.tracing.provider import ModelTrace, catalog_pricing, traced_provider

logger = logging.getLogger(__name__)

# work: the sub-run has its parent's tools; explore: only the reading and searching ones (agent/exploring.py).
Mode = Literal["work", "explore"]

MAX_SUB_RUN_MODEL_CALLS = 40
SUB_RUN_TIMEOUT_SECONDS = 30 * 60
MAX_TRANSCRIPT_OUTPUT = 2000
MAX_READERS = 3
# From least to most a run may do without asking; a sub-run never ranks above the run that started it.
ACCESS_RANK: dict[AccessLevel, int] = {"read_only": 0, "default": 1, "full": 2}


class DelegationError(Exception):
    """Delegation is not possible; the message goes back to the model."""


class ParentEnded(DelegationError):
    """The run that started the sub-run ended (its reply was stopped) while the sub-run still worked."""


@dataclass
class SubRunResult:
    status: str
    model: str
    label: str
    report: str
    changed_files: list[str]
    record_id: int | None
    mode: Mode = "work"
    # The report as the sub-run wrote it, not capped; the brief flow reads its labelled lines.
    full_report: str = ""


@dataclass
class SubRunStart:
    """What is decided and stored before a sub-run runs."""

    choice: ModelChoice
    api_key: str | None
    label: str
    record_id: int
    max_tokens: int
    outgoing: OutgoingRules
    messages: list[HistoryMessage]


@dataclass
class StreamOutcome:
    """How a sub-run's event stream ended."""

    status: str = "complete"
    texts: list[str] = field(default_factory=list)
    transcript: list[dict[str, Any]] = field(default_factory=list)
    completed: Completed | None = None
    stopped: asyncio.CancelledError | None = None

    def end(self, status: str, report: str) -> None:
        self.status, self.texts = status, [report]


@dataclass
class Delegator:
    """What a run needs to start sub-runs; built when the run starts (core/reply_run.py)."""

    session_factory: sessionmaker[Session]
    cipher: CredentialCipher
    credential_types: dict[str, CredentialType]
    providers: ProviderRegistry
    runs: RunRegistry
    internal_url: str
    layers: list[Layer]
    project_text: str | None
    working_folder: Path | None
    languages: Languages
    # The conversation's preset (for its worker model) and own model (the last fallback).
    preset: str | None
    model: str
    effort: str
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # What of the project the sub-run's system prompt and tools carry, for the audit log.
    project_data: ProjectData = NO_PROJECT_DATA
    # At most MAX_READERS exploring sub-runs at once when delegating by brief.
    readers: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(MAX_READERS))
    # The preset's delegation mode, read on first use (by_brief).
    delegation: DelegationMode | None = None

    def by_brief(self, environment_id: int) -> bool:
        """Whether the conversation's preset delegates by brief; read once, as a preset does not change mid-run."""
        if self.delegation is None:
            with self.session_factory() as session:
                self.delegation = delegation_of(session, environment_id, self.preset)
        return self.delegation == "brief"

    async def delegate(
        self,
        parent: AgentRun,
        brief: Brief,
        agent_name: str | None,
        role: Role | None,
        worker: str | None = None,
        mode: Mode = "work",
    ) -> str:
        """Hand the brief to a sub-run and return what the lead model gets back."""
        if parent.parent_token is not None:
            raise DelegationError("A delegated task cannot delegate further; do the work yourself.")
        message = brief_message(brief)
        if not self.by_brief(parent.environment_id):
            async with self.lock:
                result = await self.run_sub_run(parent, message, agent_name, role or "worker", worker, mode)
            return report_text(result)
        chosen = sub_run_role(mode, brief.check is not None, role, True)
        if mode == "explore":
            # Reading changes nothing, so a few explorations may overlap; the work below goes one at a time.
            async with self.readers:
                result = await self.run_sub_run(parent, message, agent_name, chosen, worker, mode)
            return report_text(result)
        async with self.lock:
            if brief.check is None:
                return await delegate_unchecked(self, parent, brief, agent_name, chosen, worker)
            return await delegate_by_brief(self, parent, brief, agent_name, chosen, worker)

    async def run_sub_run(
        self,
        parent: AgentRun,
        message: str,
        agent_name: str | None,
        role: Role,
        worker: str | None = None,
        mode: Mode = "work",
        instructions: BuiltinName | None = None,
    ) -> SubRunResult:
        """Run one sub-run on `message`. `instructions` names the built-in item that briefs it: the explorer when
        exploring, else the worker."""
        agent = find_agent(self.layers, agent_name) if agent_name else None
        if agent_name and agent is None:
            raise DelegationError(f"No agent named '{agent_name}'.")
        start = self.start_record(parent, message, agent, role, worker, mode)
        allowed_tools = agent.tools if agent is not None else None
        child = self.runs.child(parent, start.label, start.record_id, agent.name if agent else None, allowed_tools)
        child.change_access(sub_run_access(parent.policy.access, child.policy.access))
        if mode == "explore":
            explore_only(child)
        built_in: BuiltinName = instructions or ("explorer" if mode == "explore" else "worker")
        request = self.build_request(child, start, agent, built_in)
        entry = sub_run_entry(start.choice, request, self.project_data, parent.project_id, parent.conversation_id)
        record_request(self.session_factory, with_refusal(entry, request_refusal(request, start.outgoing)))
        spec = start.choice.spec
        provider = traced_provider(
            checked_provider(spec.create(start.api_key), start.outgoing), self.trace(child, spec.metered)
        )
        stream = await self.stream_sub_run(parent, child, start.label, start.record_id, provider, request)
        report = "".join(stream.texts).strip() or "(The sub-run ended without a report.)"
        stream.transcript.append({"type": "text", "text": report})
        changed = sorted(child.state.changed_files)
        self.store(parent, start.record_id, spec, stream.completed, stream.status, stream.transcript, child)
        if stream.stopped is not None:
            raise stream.stopped
        # The record keeps the whole report; an exploring sub-run's goes back to its parent capped.
        shown = capped_report(report) if mode == "explore" else report
        model, record_id = start.choice.model, start.record_id
        return SubRunResult(stream.status, model, start.label, shown, changed, record_id, mode, report)

    def start_record(
        self, parent: AgentRun, message: str, agent: AgentDefinition | None, role: Role, worker: str | None, mode: Mode
    ) -> SubRunStart:
        """Choose the sub-run's model and key and store its record, before anything runs."""
        request = ForSubRun(
            preset=self.preset,
            model=self.model,
            effort=self.effort,
            role="main" if role == "main" else "worker",
            worker=worker or None,
            agent_preset=agent.preset if agent is not None else None,
            agent_role=agent.role if agent is not None else None,
            project_id=parent.project_id,
        )
        messages = [HistoryMessage(role="user", content=[{"type": "text", "text": message}])]
        with self.session_factory() as session:
            choice, api_key = self.choose(session, parent, request, messages)
            label = choice.worker_label or ("worker" if role == "worker" else "main")
            if agent is not None:
                label = f"{agent.name} on {choice.worker_label}" if choice.worker_label else agent.name
            if mode == "explore":
                label = f"{label}, exploring"
            record = AgentRunRecord(
                environment_id=parent.environment_id,
                conversation_id=parent.conversation_id,
                project_id=parent.project_id,
                agent=agent.name if agent else None,
                model=choice.model,
                status="running",
                approvals=[],
                parent_run_id=parent.state.record_id,
                task=message,
                worker=choice.worker,
                started_at=datetime.now(UTC),
            )
            session.add(record)
            session.commit()
            record_id = record.id
            # The preset's reply length within the sub-run model's maximum.
            max_tokens = output_tokens(session, self.providers, parent.environment_id, self.preset, choice.model)
            outgoing = outgoing_rules(
                session, self.cipher, parent.environment_id, parent.project_id, parent.model_policy
            )
        return SubRunStart(choice, api_key, label, record_id, max_tokens, outgoing, messages)

    def build_request(
        self, child: AgentRun, start: SubRunStart, agent: AgentDefinition | None, instructions: BuiltinName
    ) -> AgentRequest:
        from app.agent.toolbox import RunToolBox  # Imported here: the toolbox imports the tools, which import this.

        system = build_system_parts(
            self.layers, self.project_text, agent_part(agent, self.layers) if agent else None, self.languages
        )
        return AgentRequest(
            model=start.choice.model,
            effort=start.choice.effort,
            system=[
                *with_listing(system, child.catalog),
                SystemPart(builtin_instructions(instructions, self.layers), source="delegation"),
            ],
            messages=start.messages,
            working_folder=self.working_folder,
            tool_server=ToolServer(url=f"{self.internal_url}/mcp", token=child.token),
            tool_box=RunToolBox(self.runs, child),
            max_model_calls=MAX_SUB_RUN_MODEL_CALLS,
            max_tokens=start.max_tokens,
        )

    async def stream_sub_run(
        self,
        parent: AgentRun,
        child: AgentRun,
        label: str,
        record_id: int,
        provider: ModelProvider,
        request: AgentRequest,
    ) -> StreamOutcome:
        """Consume the sub-run's events under the time limit; how it ended becomes its status."""
        outcome = StreamOutcome()

        async def progress(step: str) -> None:
            await parent.events.put({"event": "delegate_progress", "run_id": record_id, "label": label, "step": step})

        async def consume() -> None:
            calls: dict[str, dict[str, Any]] = {}
            async for event in provider.stream(request):
                if self.runs.find(parent.token) is None:
                    raise ParentEnded("The run that started this task has ended.")
                if isinstance(event, TextDelta):
                    outcome.texts.append(event.text)
                elif isinstance(event, ToolCallStarted):
                    outcome.texts.clear()  # The report is the text after the last tool call.
                    calls[event.id] = {"type": "tool_call", "name": event.name, "input": event.input}
                    outcome.transcript.append(calls[event.id])
                    await progress(event.name)
                elif isinstance(event, ToolCallFinished) and event.id in calls:
                    calls[event.id]["output"] = event.output[:MAX_TRANSCRIPT_OUTPUT]
                    calls[event.id]["is_error"] = event.is_error
                elif isinstance(event, Completed):
                    outcome.completed = event

        await progress("started")
        try:
            await asyncio.wait_for(consume(), timeout=SUB_RUN_TIMEOUT_SECONDS)
        except TimeoutError:
            outcome.end("incomplete", f"The task was stopped after {SUB_RUN_TIMEOUT_SECONDS // 60} minutes.")
        except ParentEnded as error:
            outcome.end("incomplete", str(error))
        except (ProviderError, DelegationError) as error:
            outcome.end("error", getattr(error, "message", None) or str(error))
        except asyncio.CancelledError as error:
            # The reply that delegated was stopped: the sub-run ends with it, its record and usage stored by the
            # caller before the stop goes on.
            outcome.end("incomplete", "The task was stopped with the reply that started it.")
            outcome.stopped = error
        finally:
            self.runs.finish(child)
            # Shielded: a stopped reply's scope keeps cancelling every await until the reply has ended.
            with anyio.CancelScope(shield=True):
                try:
                    await provider.close()
                except Exception:  # noqa: BLE001 - closing must not hide the outcome
                    logger.warning("Closing the sub-run's provider failed.", exc_info=True)
        return outcome

    def trace(self, child: AgentRun, metered: bool) -> ModelTrace | None:
        """The sub-run's model calls are steps of the sub-run, in the recorder it shares with its parent."""
        if child.trace is None or child.state.record_id is None:
            return None
        return ModelTrace(child.trace, child.state.record_id, catalog_pricing(self.providers, metered), child.secrets)

    def choose(
        self, session: Session, parent: AgentRun, request: ForSubRun, task_messages: list[HistoryMessage]
    ) -> tuple[ModelChoice, str | None]:
        """The sub-run's model and key. A refusal by the model policy is recorded in the audit log: the task, which
        is all a sub-run would have sent besides the project's part, is known by now."""
        try:
            choice = resolve_provider(session, self.providers, parent.environment_id, request)
            return choice, read_key(session, self.cipher, self.credential_types, choice, parent.conversation_id)
        except PolicyForbids as refusal:
            refused = AgentRequest(refusal.choice.model, refusal.choice.effort, [], task_messages)
            entry = sub_run_entry(refusal.choice, refused, self.project_data, parent.project_id, parent.conversation_id)
            record_request(self.session_factory, with_refusal(entry, str(refusal)))
            raise DelegationError(str(refusal)) from refusal
        except ModelChoiceError as error:
            raise DelegationError(str(error)) from error

    def store(
        self,
        parent: AgentRun,
        record_id: int,
        spec: ProviderSpec,
        completed: Completed | None,
        status: str,
        transcript: list[dict[str, Any]],
        child: AgentRun,
    ) -> None:
        try:
            with self.session_factory() as session:
                record = session.get(AgentRunRecord, record_id)
                if record is not None:
                    record.status = status
                    record.tool_calls = sum(1 for step in transcript if step["type"] == "tool_call")
                    record.approvals = list(child.state.approval_log)
                    record.transcript = transcript
                    record.finished_at = datetime.now(UTC)
                if completed is None:
                    # Cut short: the usage of the model calls that returned before it ended.
                    run = StoppedRun(
                        record_id,
                        parent.environment_id,
                        parent.conversation_id,
                        None,
                        spec.name,
                        record.model if record is not None else "",
                        "worker",
                        "metered" if spec.metered else "subscription",
                    )
                    usage = stopped_usage(session, run)
                    if usage is not None:
                        session.add(usage)
                else:
                    served = self.providers.find_model(completed.served_by_model)
                    session.add(
                        UsageRecord(
                            environment_id=parent.environment_id,
                            conversation_id=parent.conversation_id,
                            provider=spec.name,
                            model=completed.served_by_model,
                            purpose="worker",
                            input_tokens=completed.usage.input_tokens,
                            output_tokens=completed.usage.output_tokens,
                            cache_read_tokens=completed.usage.cache_read_tokens,
                            cache_write_tokens=completed.usage.cache_write_tokens,
                            cost=calculate_cost(served, completed.usage) if served and completed.billing == "metered" else None,
                            billing=completed.billing,
                            created_at=datetime.now(UTC),
                        )
                    )
                session.commit()
        except Exception:  # noqa: BLE001 - the report still goes back to the model
            logger.warning("Storing the sub-run failed.", exc_info=True)


def sub_run_access(parent: AccessLevel, child: AccessLevel) -> AccessLevel:
    """The access a sub-run works with: its own, but never more than its parent's, so the work sub-run of a
    read-only reply (/check, /plan) is read only too."""
    return parent if ACCESS_RANK[parent] < ACCESS_RANK[child] else child


def report_text(result: SubRunResult) -> str:
    if result.mode == "explore":
        return f"Exploration by {result.label} on {result.model}: {result.status}.\n\nReport:\n{result.report}"
    files = "\n".join(f"- {path}" for path in result.changed_files) or "- none written with write_file or edit_file"
    return (
        f"Sub-run by {result.label} on {result.model}: {result.status}.\n\n"
        f"Report:\n{result.report}\n\nChanged files:\n{files}"
    )

