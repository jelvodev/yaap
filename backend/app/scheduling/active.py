"""Which scheduled task, if any, a conversation's reply runs for.

Apart from the scheduler itself so the reply service can read it without importing the scheduler, which starts
replies through that service.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.storage.models import ScheduledRun, ScheduledTask

RUNNING = "running"


def active_run(session: Session, conversation_id: int) -> tuple[ScheduledRun, ScheduledTask] | None:
    run = session.scalar(
        select(ScheduledRun).where(ScheduledRun.conversation_id == conversation_id, ScheduledRun.status == RUNNING)
    )
    if run is None:
        return None
    task = session.get(ScheduledTask, run.task_id)
    return (run, task) if task is not None else None
