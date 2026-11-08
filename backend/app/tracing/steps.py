"""Reading traces: the steps of a run and the tree of a conversation's runs (tracing)."""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import get_args

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.providers.base import TokenUsage
from app.storage.models import AgentRunRecord, RunStep
from app.tracing.recorder import ApprovalOutcome, StepKind


@dataclass(frozen=True)
class StepView:
    id: int
    run_id: int
    position: int
    kind: StepKind
    started_at: datetime
    duration_ms: int
    model: str | None
    usage: TokenUsage
    cost: float | None
    model_calls: int | None
    snapshot: str | None
    tool: str | None
    summary: str | None
    output_ref: int | None
    is_error: bool
    approval: ApprovalOutcome | None


@dataclass
class RunNode:
    """A run with its sub-runs, at any depth."""

    id: int
    parent_id: int | None
    agent: str | None
    model: str
    status: str
    task: str | None
    message_id: int | None
    started_at: datetime
    finished_at: datetime | None
    children: list["RunNode"] = field(default_factory=list)
    worker: str | None = None


@dataclass(frozen=True)
class StepLocation:
    """A step with the conversation its run belongs to, so a caller can check access to it."""

    step: StepView
    conversation_id: int


def step_kind(value: str) -> StepKind:
    return "tool" if value == "tool" else "model"


def approval_outcome(value: str | None) -> ApprovalOutcome | None:
    for outcome in get_args(ApprovalOutcome):
        if value == outcome:
            return outcome
    return None


def step_view(row: RunStep) -> StepView:
    return StepView(
        id=row.id,
        run_id=row.run_id,
        position=row.position,
        kind=step_kind(row.kind),
        started_at=row.started_at,
        duration_ms=row.duration_ms,
        model=row.model,
        usage=TokenUsage(row.input_tokens, row.output_tokens, row.cache_read_tokens, row.cache_write_tokens),
        cost=row.cost,
        model_calls=row.model_calls,
        snapshot=row.snapshot,
        tool=row.tool,
        summary=row.summary,
        output_ref=row.output_ref,
        is_error=row.is_error,
        approval=approval_outcome(row.approval),
    )


def run_steps(session: Session, run_id: int) -> list[StepView]:
    """The steps of one run in the order they happened; a sub-run's steps are under the sub-run."""
    rows = session.scalars(select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.position, RunStep.id))
    return [step_view(row) for row in rows]


def steps_of_runs(session: Session, run_ids: Iterable[int]) -> dict[int, list[StepView]]:
    """The steps of several runs (a run tree) in one query, per run in the order they happened."""
    ids = list(run_ids)
    steps: dict[int, list[StepView]] = {run_id: [] for run_id in ids}
    if not ids:
        return steps
    rows = session.scalars(
        select(RunStep).where(RunStep.run_id.in_(ids)).order_by(RunStep.run_id, RunStep.position, RunStep.id)
    )
    for row in rows:
        steps[row.run_id].append(step_view(row))
    return steps


def find_step(session: Session, step_id: int) -> StepLocation | None:
    row = session.execute(
        select(RunStep, AgentRunRecord.conversation_id)
        .join(AgentRunRecord, AgentRunRecord.id == RunStep.run_id)
        .where(RunStep.id == step_id)
    ).first()
    if row is None:
        return None
    step, conversation_id = row
    return StepLocation(step_view(step), conversation_id)


def model_usage(steps: list[StepView]) -> TokenUsage:
    """The tokens of a run's model calls together: the reply's usage record."""
    models = [step.usage for step in steps if step.kind == "model"]
    return TokenUsage(
        input_tokens=sum(usage.input_tokens for usage in models),
        output_tokens=sum(usage.output_tokens for usage in models),
        cache_read_tokens=sum(usage.cache_read_tokens for usage in models),
        cache_write_tokens=sum(usage.cache_write_tokens for usage in models),
    )


def steps_cost(steps: Iterable[StepView]) -> float | None:
    """What the model calls cost together; None when none has a cost (a subscription, or no model call)."""
    costs = [step.cost for step in steps if step.cost is not None]
    return sum(costs) if costs else None


def conversation_runs(session: Session, conversation_id: int) -> list[RunNode]:
    """The conversation's runs as a tree: top-level runs (one per reply) in order, each with its sub-runs."""
    records = session.scalars(
        select(AgentRunRecord).where(AgentRunRecord.conversation_id == conversation_id).order_by(AgentRunRecord.id)
    )
    nodes = {
        record.id: RunNode(
            id=record.id,
            parent_id=record.parent_run_id,
            agent=record.agent,
            model=record.model,
            status=record.status,
            task=record.task,
            message_id=record.message_id,
            started_at=record.started_at,
            finished_at=record.finished_at,
            worker=record.worker,
        )
        for record in records
    }
    roots: list[RunNode] = []
    for node in nodes.values():
        parent = nodes.get(node.parent_id) if node.parent_id is not None else None
        (parent.children if parent is not None else roots).append(node)
    return roots


def walk(nodes: Iterable[RunNode]) -> Iterator[RunNode]:
    """Every run of a tree, each before its sub-runs."""
    for node in nodes:
        yield node
        yield from walk(node.children)


def run_tree(session: Session, conversation_id: int, run_id: int) -> RunNode | None:
    """One run of the conversation with its sub-runs at any depth; None when the conversation has no such run."""
    return next((node for node in walk(conversation_runs(session, conversation_id)) if node.id == run_id), None)


@dataclass(frozen=True)
class RunTrace:
    """A run with its sub-runs at any depth and the steps of each, by run."""

    root: RunNode
    steps: dict[int, list[StepView]]


def run_trace(session: Session, conversation_id: int, run_id: int) -> RunTrace | None:
    root = run_tree(session, conversation_id, run_id)
    if root is None:
        return None
    return RunTrace(root, steps_of_runs(session, (node.id for node in walk([root]))))


def reply_run_id(session: Session, conversation_id: int, message_id: int) -> int | None:
    """The top-level run that produced a reply, the root of the reply's trace."""
    return session.scalar(
        select(AgentRunRecord.id)
        .where(
            AgentRunRecord.conversation_id == conversation_id,
            AgentRunRecord.message_id == message_id,
            AgentRunRecord.parent_run_id.is_(None),
        )
        .order_by(AgentRunRecord.id.desc())
        .limit(1)
    )
