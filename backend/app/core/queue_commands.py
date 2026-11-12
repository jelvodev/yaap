"""Commands in the message queue: /compact and /eval are sent on their own and leave the queue once accepted."""

import re
from typing import Literal

from sqlalchemy import delete, update
from sqlalchemy.orm import Session, sessionmaker

from app.core.summaries import compact_focus
from app.storage.models import QueuedMessage

QueueItemKind = Literal["message", "compact", "eval"]

# /eval, or /eval <case>: an eval run of all the project's cases or of one.
EVAL_COMMAND = re.compile(r"^/eval(?:\s+([a-z0-9][a-z0-9-]{0,59}))?$")


def queue_item_kind(text: str) -> QueueItemKind:
    """What a queued text is when it is sent: an ordinary message, or a command that does not start a reply."""
    if compact_focus(text) is not None:
        return "compact"
    if EVAL_COMMAND.match(text.strip()) is not None:
        return "eval"
    return "message"


def claim_items(
    session_factory: sessionmaker[Session],
    conversation_id: int,
    item_ids: tuple[int, ...],
    token: str,
    current: str | None,
) -> bool:
    """Mark queue rows as being run by `token`, only while all of them are still there and carry `current` as their
    handoff (None: no handoff). Atomic, so two senders of the same command cannot both run it."""
    with session_factory() as session:
        handoff = (
            QueuedMessage.send_now_job_id.is_(None) if current is None else QueuedMessage.send_now_job_id == current
        )
        result = session.execute(
            update(QueuedMessage)
            .where(
                QueuedMessage.conversation_id == conversation_id,
                QueuedMessage.id.in_(item_ids),
                QueuedMessage.steering_run_id.is_(None),
                handoff,
            )
            .values(send_now_job_id=token)
        )
        if result.rowcount != len(item_ids):  # type: ignore[attr-defined]  # DML results carry a rowcount
            session.rollback()
            return False
        session.commit()
        return True


def consume_items(session: Session, conversation_id: int, item_ids: tuple[int, ...]) -> None:
    """Remove queue rows whose command was accepted."""
    session.execute(
        delete(QueuedMessage).where(QueuedMessage.conversation_id == conversation_id, QueuedMessage.id.in_(item_ids))
    )
    session.commit()


def release_items(session_factory: sessionmaker[Session], conversation_id: int, token: str) -> None:
    """Return rows claimed by `token` to the ordinary queue, as when the command did not finish."""
    with session_factory() as session:
        session.execute(
            update(QueuedMessage)
            .where(QueuedMessage.conversation_id == conversation_id, QueuedMessage.send_now_job_id == token)
            .values(send_now_job_id=None)
        )
        session.commit()
