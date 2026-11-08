"""Messages queued in a conversation while a reply runs, sent one by one after it."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.storage.models import QueuedMessage


def queued_items(session: Session, conversation_id: int) -> list[QueuedMessage]:
    return list(
        session.scalars(
            select(QueuedMessage)
            .where(QueuedMessage.conversation_id == conversation_id)
            .order_by(QueuedMessage.position, QueuedMessage.id)
        )
    )


def queue_undelivered_steering(session_factory: sessionmaker[Session], conversation_id: int, texts: list[str]) -> None:
    """Steering messages the agent did not get before the reply ended go first in the queue."""
    with session_factory() as session:
        items = queued_items(session, conversation_id)
        first = min((item.position for item in items), default=1)
        for offset, text in enumerate(texts):
            session.add(
                QueuedMessage(
                    conversation_id=conversation_id,
                    text=text,
                    attachment_ids=[],
                    position=first - len(texts) + offset,
                    created_at=datetime.now(UTC),
                )
            )
        session.commit()
