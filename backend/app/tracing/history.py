"""The earlier runs a cost estimate is made from, with their totals from the steps.

A goal and a scheduled task's run both span several replies, each a top-level run with its sub-runs. Their total is
the sum of the model steps of every run record of the conversation that started while the goal or task run was
open, sub-runs included, as the trace shows them.

- For a goal: the ended goals of the same project whose conversation works as the same agent (none counts as an
  agent of its own), outside scheduled tasks, whose runs would otherwise crowd out the user's own goals.
- For a scheduled task: the task's finished runs.

Only the most recent HISTORY of them count, so the estimate follows how the work goes now, and only those that made
a model call: a run refused at its start says nothing about cost.
"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.goals import OPEN_STATUSES
from app.storage.models import AgentRunRecord, Conversation, Goal, RunStep, ScheduledRun
from app.tracing.estimate import RunTotal

HISTORY = 20
STEP_TOKENS = RunStep.input_tokens + RunStep.output_tokens + RunStep.cache_read_tokens + RunStep.cache_write_tokens


def window_total(session: Session, conversation_id: int, start: datetime, end: datetime | None) -> RunTotal | None:
    """The model steps of the conversation's runs that started in the window; None when there were none."""
    query = (
        select(
            func.count(RunStep.id), func.coalesce(func.sum(RunStep.cost), 0.0), func.coalesce(func.sum(STEP_TOKENS), 0)
        )
        .join(AgentRunRecord, AgentRunRecord.id == RunStep.run_id)
        .where(
            AgentRunRecord.conversation_id == conversation_id,
            AgentRunRecord.started_at >= start,
            RunStep.kind == "model",
        )
    )
    if end is not None:
        query = query.where(AgentRunRecord.started_at <= end)
    calls, cost, tokens = session.execute(query).one()
    return RunTotal(float(cost), int(tokens)) if calls else None


def goal_history(session: Session, project_id: int, agent: str | None) -> list[RunTotal]:
    """The totals of the project's most recent ended goals worked on as the agent."""
    query = (
        select(Goal)
        .join(Conversation, Conversation.id == Goal.conversation_id)
        .where(
            Conversation.project_id == project_id,
            Conversation.agent.is_(None) if agent is None else Conversation.agent == agent,
            Conversation.scheduled_task_id.is_(None),
            Goal.status.not_in(OPEN_STATUSES),
            Goal.finished_at.is_not(None),
        )
        .order_by(Goal.finished_at.desc())
        .limit(HISTORY)
    )
    goals = session.scalars(query).all()
    totals = (window_total(session, goal.conversation_id, goal.created_at, goal.finished_at) for goal in goals)
    return [total for total in totals if total is not None]


def task_history(session: Session, task_id: int) -> list[RunTotal]:
    """The totals of the task's most recent finished runs."""
    query = (
        select(ScheduledRun)
        .where(
            ScheduledRun.task_id == task_id,
            ScheduledRun.conversation_id.is_not(None),
            ScheduledRun.finished_at.is_not(None),
        )
        .order_by(ScheduledRun.finished_at.desc())
        .limit(HISTORY)
    )
    runs = session.scalars(query).all()
    totals = (
        window_total(session, run.conversation_id, run.started_at, run.finished_at)
        for run in runs
        if run.conversation_id is not None
    )
    return [total for total in totals if total is not None]
