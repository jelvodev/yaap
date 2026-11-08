"""Utility tasks that were skipped, as the app shows them: read back from the audit log.

`run_utility` records each task the model policy (or an outgoing check) refuses as a `refused` row with purpose
`utility` and the task. That row is the only record of the skip, so the chat, the reply stream and the project panel
read it from there instead of keeping a second list: under a reply, the tasks skipped since the user's message; in a
project, the background tasks whose latest attempt was refused.
"""

from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.utility import UtilitySkipped
from app.core.utility_tasks import task_label
from app.storage.models import AuditRecord, Message

# The background work of a project whose skips the project panel shows.
PROJECT_TASKS: tuple[str, ...] = ("memory_tidy", "learning")


class UtilitySkipOut(BaseModel):
    """A utility task that was not sent, and why; the same shape in the API and in the reply stream."""

    # The audit row, so the page can tell a skip it has shown from a new one.
    id: int
    time: datetime
    task: str | None
    label: str
    reason: str


def skip_out(record: AuditRecord) -> UtilitySkipOut:
    return UtilitySkipOut(
        id=record.id,
        time=record.created_at,
        task=record.task,
        label=task_label(record.task),
        reason=record.reason or "Refused before it was sent.",
    )


def refused_utility() -> Select[tuple[AuditRecord]]:
    return select(AuditRecord).where(AuditRecord.purpose == "utility", AuditRecord.outcome == "refused")


def conversation_skips(session: Session, conversation_id: int, since: datetime | None = None) -> list[UtilitySkipOut]:
    """The utility tasks of the conversation that were skipped since `since`, oldest first; without it, since the
    user's latest message, so the notice belongs to the last exchange."""
    if since is None:
        since = session.scalar(
            select(func.max(Message.created_at)).where(
                Message.conversation_id == conversation_id, Message.role == "user"
            )
        )
        if since is None:
            return []
    query = refused_utility().where(AuditRecord.conversation_id == conversation_id, AuditRecord.created_at >= since)
    return [skip_out(record) for record in session.scalars(query.order_by(AuditRecord.id))]


def skipped_since(
    session_factory: sessionmaker[Session], conversation_id: int, since: datetime
) -> list[UtilitySkipOut]:
    """conversation_skips() in a session of its own, for the reply stream."""
    with session_factory() as session:
        return conversation_skips(session, conversation_id, since)


def project_skips(session: Session, project_id: int, tasks: tuple[str, ...] = PROJECT_TASKS) -> list[UtilitySkipOut]:
    """Per task, its latest attempt in the project when that was refused; a task that ran since shows nothing."""
    skipped: list[UtilitySkipOut] = []
    for task in tasks:
        latest = session.scalar(
            select(AuditRecord)
            .where(AuditRecord.purpose == "utility", AuditRecord.project_id == project_id, AuditRecord.task == task)
            .order_by(AuditRecord.created_at.desc(), AuditRecord.id.desc())
            .limit(1)
        )
        if latest is not None and latest.outcome == "refused":
            skipped.append(skip_out(latest))
    return skipped


def project_skip_message(session: Session, project_id: int, task: str) -> str | None:
    """What to tell the user who asked for a project task that gave nothing, when its latest attempt was skipped."""
    found = project_skips(session, project_id, (task,))
    return UtilitySkipped(task, found[0].reason).message if found else None
