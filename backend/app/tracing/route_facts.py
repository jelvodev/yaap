"""What the route of an answer is built from: the recorded steps of a reply, from the database or from a running reply.

A `RouteStep` is one recorded step in the same form either way, so one builder serves the API and the live stream.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.core.why import Why
from app.providers.base import TokenUsage
from app.tracing.notes import ApprovalOutcome, Note, NoteView
from app.tracing.recorder import ModelStep, NoteStep, StepKind, ToolStep
from app.tracing.steps import StepView


@dataclass(frozen=True)
class RouteStep:
    run_id: int
    position: int
    kind: StepKind
    started_at: datetime
    duration_ms: int = 0
    model: str | None = None
    usage: TokenUsage = field(default_factory=TokenUsage)
    cost: float | None = None
    tool: str | None = None
    summary: str | None = None
    is_error: bool = False
    approval: ApprovalOutcome | None = None
    output_ref: int | None = None
    why: Why | None = None
    note: Note | None = None
    # The row id; None for a step seen live.
    step_id: int | None = None


@dataclass(frozen=True)
class WorkerRun:
    run_id: int
    label: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    status: str = "complete"
    task: str | None = None


@dataclass(frozen=True)
class UtilityUse:
    """A helper model call made for the reply after (or beside) it: a title, memory suggestions, a summary."""

    task: str
    model: str | None
    cost: float | None
    created_at: datetime
    summary: str | None = None


@dataclass(frozen=True)
class WaitingCall:
    """A tool call that waits for the user's decision; it has no step yet."""

    run_id: int
    tool: str
    summary: str | None
    since: datetime
    # The position the call reserved, when known.
    position: int | None = None


@dataclass(frozen=True)
class RouteFacts:
    # The route stored with the reply (core/routing.py); None when the preset does not route.
    route: dict[str, Any] | None
    main_run_id: int
    workers: list[WorkerRun] = field(default_factory=list)
    steps: list[RouteStep] = field(default_factory=list)
    afterwards: list[UtilityUse] = field(default_factory=list)
    waiting: list[WaitingCall] = field(default_factory=list)
    # The status of the reply's run: running, complete, incomplete, refused or error.
    reply_status: str = "complete"


def step_from_view(view: StepView, why: Why | None = None) -> RouteStep:
    return RouteStep(
        run_id=view.run_id,
        position=view.position,
        kind=view.kind,
        started_at=view.started_at,
        duration_ms=view.duration_ms,
        model=view.model,
        usage=view.usage,
        cost=view.cost,
        tool=view.tool,
        summary=view.summary,
        is_error=view.is_error,
        approval=view.approval,
        output_ref=view.output_ref,
        why=why,
        step_id=view.id,
    )


def step_from_note(view: NoteView) -> RouteStep:
    return RouteStep(
        run_id=view.run_id,
        position=view.position,
        kind="note",
        started_at=view.started_at,
        summary=view.note.text,
        note=view.note,
        step_id=view.step_id,
    )


def live_step(run_id: int, step: ModelStep | ToolStep | NoteStep) -> RouteStep:
    """A step as the recorder sees it while the reply runs."""
    if isinstance(step, NoteStep):
        return RouteStep(
            run_id, step.position, "note", step.started_at, summary=step.note.text, note=step.note
        )
    if isinstance(step, ModelStep):
        return RouteStep(
            run_id,
            step.position,
            "model",
            step.started_at,
            step.duration_ms,
            model=step.model,
            usage=step.usage,
            cost=step.cost,
        )
    return RouteStep(
        run_id,
        step.position,
        "tool",
        step.started_at,
        step.duration_ms,
        tool=step.tool,
        summary=step.summary,
        is_error=step.is_error,
        approval=step.approval,
        output_ref=step.output_ref,
        why=step.why,
    )
