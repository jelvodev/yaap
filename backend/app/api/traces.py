"""The trace of a reply (tracing): its run tree, the steps of each run, and what each model call saw.

Every route is under its conversation and reads it through the conversation APIs' own dependency, so a trace is open
exactly where its conversation is; a run, message or step of another conversation is not found.
"""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.conversations import ConversationDependency
from app.api.dependencies import DataDirDependency, SessionDependency
from app.tracing.context_view import part_content, step_context
from app.tracing.notes import ApprovalOutcome
from app.tracing.snapshots import SnapshotStore, trace_dir
from app.tracing.steps import (
    RunNode,
    RunTrace,
    StepView,
    find_step,
    reply_run_id,
    run_trace,
    steps_cost,
    walk,
)

router = APIRouter(tags=["traces"])

NO_TRACE = "This reply has no trace."


class TraceStepOut(BaseModel):
    id: int
    position: int
    kind: Literal["model", "tool"]
    started_at: datetime
    duration_ms: int
    # Model calls: the model, its tokens and cost (none on a subscription), how many calls it stands for, and whether
    # its context snapshot was recorded.
    model: str | None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost: float | None
    model_calls: int | None
    has_context: bool
    # Tool calls: the tool, a summary of its arguments, its stored whole output, and the approval decision.
    tool: str | None
    summary: str | None
    output_ref: int | None
    is_error: bool
    approval: ApprovalOutcome | None


class TraceRunOut(BaseModel):
    id: int
    parent_run_id: int | None
    agent: str | None
    model: str
    worker: str | None
    status: str
    task: str | None
    message_id: int | None
    started_at: datetime
    finished_at: datetime | None
    # Empty while the run goes on.
    duration_ms: int | None
    # What this run's own model calls cost, and with its sub-runs; empty when nothing had a cost.
    cost: float | None
    total_cost: float | None
    steps: list[TraceStepOut]
    # Sub-runs in the order they started.
    children: list["TraceRunOut"]


class TraceOut(BaseModel):
    conversation_id: int
    run: TraceRunOut


class ContextPartOut(BaseModel):
    # Asks for the content: system-0, tools, message-3.
    key: str
    label: str
    # Characters of the content; empty when its blob was pruned.
    chars: int | None


class ContextGroupOut(BaseModel):
    # A system part's source (base, rules, standards, skills, agent, project, ...), tools or messages.
    source: str
    chars: int
    parts: list[ContextPartOut]


class StepContextOut(BaseModel):
    step_id: int
    # False when the snapshot, or a part of it, was pruned (snapshots are kept 30 days after last use).
    available: bool
    complete: bool
    chars: int
    groups: list[ContextGroupOut]


class ContextPartContentOut(BaseModel):
    key: str
    content: str


def step_out(step: StepView) -> TraceStepOut:
    return TraceStepOut(
        id=step.id,
        position=step.position,
        kind=step.kind,
        started_at=step.started_at,
        duration_ms=step.duration_ms,
        model=step.model,
        input_tokens=step.usage.input_tokens,
        output_tokens=step.usage.output_tokens,
        cache_read_tokens=step.usage.cache_read_tokens,
        cache_write_tokens=step.usage.cache_write_tokens,
        cost=step.cost,
        model_calls=step.model_calls,
        has_context=step.snapshot is not None,
        tool=step.tool,
        summary=step.summary,
        output_ref=step.output_ref,
        is_error=step.is_error,
        approval=step.approval,
    )


def run_out(node: RunNode, trace: RunTrace) -> TraceRunOut:
    steps = trace.steps.get(node.id, [])
    subtree = [step for run in walk([node]) for step in trace.steps.get(run.id, [])]
    finished = node.finished_at
    return TraceRunOut(
        id=node.id,
        parent_run_id=node.parent_id,
        agent=node.agent,
        model=node.model,
        worker=node.worker,
        status=node.status,
        task=node.task,
        message_id=node.message_id,
        started_at=node.started_at,
        finished_at=finished,
        duration_ms=int((finished - node.started_at).total_seconds() * 1000) if finished is not None else None,
        cost=steps_cost(steps),
        total_cost=steps_cost(subtree),
        steps=[step_out(step) for step in steps],
        children=[run_out(child, trace) for child in node.children],
    )


def trace_out(session: Session, conversation_id: int, run_id: int) -> TraceOut:
    trace = run_trace(session, conversation_id, run_id)
    if trace is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_TRACE)
    return TraceOut(conversation_id=conversation_id, run=run_out(trace.root, trace))


def located_snapshot(session: Session, conversation_id: int, step_id: int) -> str | None:
    """The snapshot digest of a model step of the conversation; 404 when the conversation has no such step."""
    located = find_step(session, step_id)
    if located is None or located.conversation_id != conversation_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Step not found in this conversation.")
    return located.step.snapshot


@router.get("/conversations/{conversation_id}/messages/{message_id}/trace", response_model=TraceOut)
def read_reply_trace(conversation: ConversationDependency, message_id: int, session: SessionDependency) -> TraceOut:
    """The trace of a reply: the run that produced it, its sub-runs at any depth, and the steps of each."""
    run_id = reply_run_id(session, conversation.id, message_id)
    if run_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_TRACE)
    return trace_out(session, conversation.id, run_id)


@router.get("/conversations/{conversation_id}/runs/{run_id}/trace", response_model=TraceOut)
def read_run_trace(conversation: ConversationDependency, run_id: int, session: SessionDependency) -> TraceOut:
    """The trace of one run of the conversation (a reply's run or a sub-run), with its sub-runs and steps."""
    return trace_out(session, conversation.id, run_id)


@router.get("/conversations/{conversation_id}/steps/{step_id}/context", response_model=StepContextOut)
def read_step_context(
    conversation: ConversationDependency, step_id: int, session: SessionDependency, data_dir: DataDirDependency
) -> StepContextOut:
    """What a model call saw, grouped by source, with the size of each part; the content is read per part."""
    digest = located_snapshot(session, conversation.id, step_id)
    context = step_context(SnapshotStore(trace_dir(data_dir)), digest) if digest else None
    if context is None:
        return StepContextOut(step_id=step_id, available=False, complete=False, chars=0, groups=[])
    return StepContextOut(
        step_id=step_id,
        available=True,
        complete=context.complete,
        chars=context.chars,
        groups=[
            ContextGroupOut(
                source=group.source,
                chars=group.chars,
                parts=[ContextPartOut(key=part.key, label=part.label, chars=part.chars) for part in group.parts],
            )
            for group in context.groups
        ],
    )


@router.get("/conversations/{conversation_id}/steps/{step_id}/context/{key}", response_model=ContextPartContentOut)
def read_context_part(
    conversation: ConversationDependency,
    step_id: int,
    key: str,
    session: SessionDependency,
    data_dir: DataDirDependency,
) -> ContextPartContentOut:
    """The content of one part of what a model call saw."""
    digest = located_snapshot(session, conversation.id, step_id)
    content = part_content(SnapshotStore(trace_dir(data_dir)), digest, key) if digest else None
    if content is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This part of the context is gone; snapshots are kept 30 days.")
    return ContextPartContentOut(key=key, content=content)
