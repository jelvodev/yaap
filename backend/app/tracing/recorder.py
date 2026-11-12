"""The trace recorder: one `RunStep` per model call and per tool call.

A reply's run carries one recorder in its `RunServices`; a sub-run made by `RunRegistry.child` shares it, and writes
its steps under its own run record. The position of a step is taken when the step starts (a tool call) or when the
model call ends, so the steps of a run read in the order they happened even when they are written later.

Model calls are recorded at the common entry of the provider adapters (`tracing.provider`), tool calls in the gate
(`agent.gate`). A step that cannot be written is logged and the run goes on: the trace serves the user, the run
serves the work.
"""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import anyio
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.why import Why
from app.providers.base import CacheTtl, TokenUsage
from app.secrets.service import mask
from app.storage.models import RunStep
from app.tracing.notes import ApprovalOutcome, Note, note_row
from app.tracing.snapshots import ContextSnapshot, SnapshotStore

logger = logging.getLogger(__name__)

StepKind = Literal["model", "tool", "note"]
# What a tool step tells about the work, for learning proposals (app/learning): the user declined it, a test command
# failed for the second time in the run, or it searched for what the Blueprint or the memory already held.
SignalKind = Literal["declined", "test_failed_twice", "known_search"]
SIGNAL_NOTE_CHARS = 500
SUMMARY_CHARS = 300
# How a model step's summary starts when its cacheable system parts were cached for an hour.
CACHE_HOUR_NOTE = "Prompt cache: 1 hour"


@dataclass(frozen=True)
class ModelStep:
    position: int
    started_at: datetime
    duration_ms: int
    model: str
    usage: TokenUsage
    cost: float | None = None
    # How many model calls the step stands for: 1 for an adapter's own call, what a CLI reports, None when unknown.
    model_calls: int | None = 1
    snapshot: str | None = None
    # How long the call asked its cacheable system parts to be cached.
    cache_ttl: CacheTtl = "5m"


@dataclass(frozen=True)
class ToolStep:
    position: int
    started_at: datetime
    duration_ms: int
    tool: str
    summary: str
    is_error: bool = False
    approval: ApprovalOutcome | None = None
    # The number of the stored whole output (read_output), when the output was long.
    output_ref: int | None = None
    signal: SignalKind | None = None
    # The user's reason for declining, the failed test command, or the Blueprint item that held what was searched.
    signal_note: str | None = None
    # Why the call asked or was refused, and the key of the call it belongs to.
    why: Why | None = None
    call_key: str | None = None


@dataclass(frozen=True)
class NoteStep:
    position: int
    started_at: datetime
    note: Note


def argument_summary(description: str | None, arguments: dict[str, object], secret_values: dict[str, str]) -> str:
    """A short line for a tool call: the tool's own description of it, or its arguments as compact JSON."""
    text = description or json.dumps(arguments, ensure_ascii=False, default=str, separators=(",", ":"))
    text = mask(" ".join(text.split()), secret_values)
    return text if len(text) <= SUMMARY_CHARS else text[: SUMMARY_CHARS - 1] + "…"


def cache_note(ttl: CacheTtl, usage: TokenUsage) -> str | None:
    """The line a model step keeps when its system parts were cached for an hour, with what was written at the hour's
    price, so the traces show where the hour pays (reads against writes); None for the usual five minutes. Kept in
    the step's summary, which model steps have no other use for."""
    if ttl != "1h":
        return None
    return f"{CACHE_HOUR_NOTE}; {usage.cache_write_1h_tokens} tokens written for an hour"


def step_row(run_id: int, step: ModelStep | ToolStep | NoteStep) -> RunStep:
    if isinstance(step, NoteStep):
        return note_row(run_id, step.position, step.started_at, step.note)
    row = RunStep(
        run_id=run_id,
        position=step.position,
        started_at=step.started_at,
        duration_ms=max(step.duration_ms, 0),
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=0,
        is_error=False,
    )
    if isinstance(step, ModelStep):
        row.kind = "model"
        row.model = step.model[:80]
        row.input_tokens = step.usage.input_tokens
        row.output_tokens = step.usage.output_tokens
        row.cache_read_tokens = step.usage.cache_read_tokens
        row.cache_write_tokens = step.usage.cache_write_tokens
        row.cost = step.cost
        row.model_calls = step.model_calls
        row.snapshot = step.snapshot
        row.summary = cache_note(step.cache_ttl, step.usage)
    else:
        row.kind = "tool"
        row.tool = step.tool[:120]
        row.summary = step.summary[:SUMMARY_CHARS]
        row.is_error = step.is_error
        row.approval = step.approval
        row.output_ref = step.output_ref
        row.signal = step.signal
        row.signal_note = step.signal_note[:SIGNAL_NOTE_CHARS] if step.signal_note else None
        if step.why is not None:
            row.detail = {"why": step.why.stored(), "call_key": step.call_key}
    return row


# Told about every step as it is recorded (run record id, step), on the event loop: an eval run watches its budget
# this way. It must not block.
StepListener = Callable[[int, ModelStep | ToolStep], None]


class TraceRecorder:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        snapshots: SnapshotStore | None = None,
        listener: StepListener | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.snapshots = snapshots
        self.listener = listener
        self._positions: dict[int, int] = {}

    def reserve(self, run_id: int) -> int:
        """The next position in the run. Called on the event loop, so positions follow the order of the calls."""
        position = self._positions.get(run_id, 0) + 1
        self._positions[run_id] = position
        return position

    def write(self, run_id: int, step: ModelStep | ToolStep | NoteStep) -> None:
        with self.session_factory() as session:
            session.add(step_row(run_id, step))
            session.commit()

    async def record(self, run_id: int, step: ModelStep | ToolStep | NoteStep) -> None:
        try:
            await anyio.to_thread.run_sync(self.write, run_id, step)
        except SQLAlchemyError:
            logger.warning("Recording a step of run %s failed.", run_id, exc_info=True)
        # Notes are not model or tool calls: an eval budget counts steps through the listener.
        if self.listener is not None and not isinstance(step, NoteStep):
            self.listener(run_id, step)

    async def note(self, run_id: int, note: Note) -> None:
        """Record a note at the next position of the run."""
        await self.record(run_id, NoteStep(self.reserve(run_id), datetime.now(UTC), note))

    def note_now(self, run_id: int, note: Note) -> None:
        """Record a note from a worker thread before the run streams. Nothing else reserves positions of this run
        at that moment (its steps start with the stream), so the plain dict update is safe off the event loop."""
        step = NoteStep(self.reserve(run_id), datetime.now(UTC), note)
        try:
            self.write(run_id, step)
        except SQLAlchemyError:
            logger.warning("Recording a step of run %s failed.", run_id, exc_info=True)

    async def snapshot(self, context: ContextSnapshot, secret_values: dict[str, str]) -> str | None:
        """Store what a model call saw; None without a store or when it cannot be written."""
        if self.snapshots is None:
            return None
        try:
            return await anyio.to_thread.run_sync(self.snapshots.save, context, secret_values)
        except OSError:
            logger.warning("Storing a context snapshot failed.", exc_info=True)
            return None
