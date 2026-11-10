"""What is working now, so an update does not cut it off: read from the database, never from memory."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.goals import ACTIVE
from app.storage.models import AgentRunRecord, EvalRun, Goal, Replay, ScheduledTask

BusyKind = Literal["reply", "goal", "eval_run", "replay", "scheduled_task"]
OPEN_STATUSES = ("queued", "running")
SOON = timedelta(minutes=10)


@dataclass(frozen=True)
class BusyItem:
    kind: BusyKind
    id: int
    label: str


def find_busy(session: Session, now: datetime, soon: timedelta = SOON) -> list[BusyItem]:
    """Replies and runs in progress, active goals, eval runs and replays that are queued or running, and scheduled
    tasks that run now or are due within `soon`. Finished work is not listed."""
    found: list[BusyItem] = []
    for run in session.scalars(select(AgentRunRecord).where(AgentRunRecord.status == "running")):
        found.append(BusyItem("reply", run.id, f"a reply in conversation {run.conversation_id}"))
    for goal in session.scalars(select(Goal).where(Goal.status == ACTIVE)):
        found.append(BusyItem("goal", goal.id, f"a goal in conversation {goal.conversation_id}"))
    for eval_run in session.scalars(select(EvalRun).where(EvalRun.status.in_(OPEN_STATUSES))):
        found.append(BusyItem("eval_run", eval_run.id, f"eval run {eval_run.id}"))
    for replay in session.scalars(select(Replay).where(Replay.status.in_(OPEN_STATUSES))):
        found.append(BusyItem("replay", replay.id, f"replay {replay.id}"))
    scheduled_soon = ScheduledTask.enabled.is_(True) & (ScheduledTask.next_run_at <= now + soon)
    due = ScheduledTask.running.is_(True) | scheduled_soon
    for task in session.scalars(select(ScheduledTask).where(due)):
        found.append(BusyItem("scheduled_task", task.id, f"scheduled task {task.name!r}"))
    return found
