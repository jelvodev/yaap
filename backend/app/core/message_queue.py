"""Messages queued in a conversation while a reply runs, sent one by one after it."""

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.agent.runs import SteeringHandoff
from app.storage.models import QueuedMessage

MAX_MESSAGE_LENGTH = 200_000
MAX_MESSAGE_ATTACHMENTS = 20
MAX_QUEUED_MESSAGES = 20


class QueueSteeringHandoff(SteeringHandoff):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def claim(self, conversation_id: int, run_id: int) -> list[tuple[int, str]]:
        rows = claim_steering(self.session_factory, conversation_id, run_id)
        return [(row.id, row.text) for row in rows]

    def acknowledge(self, run_id: int, item_ids: list[int]) -> None:
        acknowledge_steering(self.session_factory, run_id, item_ids)

    def restore(self, run_id: int) -> None:
        restore_steering(self.session_factory, run_id)


class QueueError(Exception):
    """A queue operation that cannot be accepted, with a safe message for the API."""


def queued_items(session: Session, conversation_id: int) -> list[QueuedMessage]:
    return list(
        session.scalars(
            select(QueuedMessage)
            .where(QueuedMessage.conversation_id == conversation_id)
            .order_by(QueuedMessage.position, QueuedMessage.id)
        )
    )


def select_for_send(
    session: Session, conversation_id: int, item_id: int | None, send_all: bool
) -> list[QueuedMessage]:
    """Read the selected queue rows and validate the combined message before changing either one."""
    items = queued_items(session, conversation_id)
    if send_all:
        selected = items
    elif item_id is not None:
        selected = [item for item in items if item.id == item_id]
    else:
        selected = items[:1]
    selected = validate_selection(selected)
    if any(item.send_now_job_id is not None or item.steering_run_id is not None for item in selected):
        raise QueueError("A selected queued message already has a pending handoff.")
    return selected


def select_ids_for_send(
    session: Session, conversation_id: int, item_ids: list[int], job_id: str
) -> list[QueuedMessage]:
    """Read an exact persisted selection, preserving the order shown in the queue."""
    if not item_ids:
        raise QueueError("The selected queued message is no longer available.")
    selected = [item for item in queued_items(session, conversation_id) if item.id in set(item_ids)]
    if len(selected) != len(set(item_ids)):
        raise QueueError("A selected queued message is no longer available.")
    if any(item.send_now_job_id != job_id for item in selected):
        raise QueueError("The saved send-now selection has changed.")
    return validate_selection(selected)


def validate_selection(selected: list[QueuedMessage]) -> list[QueuedMessage]:
    if not selected:
        raise QueueError("The selected queued message is no longer available.")
    text = "\n\n".join(item.text for item in selected if item.text)
    attachment_ids = [attachment_id for item in selected for attachment_id in (item.attachment_ids or [])]
    if len(text) > MAX_MESSAGE_LENGTH:
        raise QueueError(f"The combined message must be at most {MAX_MESSAGE_LENGTH:,} characters.")
    if len(attachment_ids) > MAX_MESSAGE_ATTACHMENTS or len(set(attachment_ids)) != len(attachment_ids):
        raise QueueError(f"A message can contain at most {MAX_MESSAGE_ATTACHMENTS} distinct attachments.")
    if not text.strip() and not attachment_ids:
        raise QueueError("The selected queued message is empty.")
    return selected


def request_send_now(
    session: Session, conversation_id: int, item_id: int | None, send_all: bool, job_id: str
) -> list[int]:
    """Mark a selection for one running reply without removing it from the durable queue."""
    selected = select_for_send(session, conversation_id, item_id, send_all)
    for item in selected:
        item.send_now_job_id = job_id
    session.commit()
    return [item.id for item in selected]


def add_steering(
    session: Session, conversation_id: int, run_id: int, text: str, item_id: int | None = None
) -> int:
    """Mark a queue row for delivery by one run while retaining it for retry until acknowledgement."""
    if item_id is None:
        items = queued_items(session, conversation_id)
        if len(items) >= MAX_QUEUED_MESSAGES:
            raise QueueError(f"At most {MAX_QUEUED_MESSAGES} messages can wait in the queue.")
        if not text.strip():
            raise QueueError("A steering message needs text.")
        item = QueuedMessage(
            conversation_id=conversation_id,
            text=text,
            attachment_ids=[],
            position=max((queued.position for queued in items), default=0) + 1,
            created_at=datetime.now(UTC),
        )
        session.add(item)
        session.flush()
    else:
        item = session.get(QueuedMessage, item_id)
        if item is None or item.conversation_id != conversation_id:
            raise QueueError("Queued message not found.")
        if item.attachment_ids:
            raise QueueError("A message with attachments waits for the reply to end.")
        if item.steering_run_id is not None or item.send_now_job_id is not None:
            raise QueueError("That message is already being steered to a running reply.")
    item.steering_run_id = run_id
    item.steering_claimed = False
    session.commit()
    return item.id


def claim_steering(session_factory: sessionmaker[Session], conversation_id: int, run_id: int) -> list[QueuedMessage]:
    """Claim pending steering rows once for a tool-result handoff."""
    with session_factory() as session:
        ids = list(
            session.scalars(
                select(QueuedMessage.id)
                .where(
                    QueuedMessage.conversation_id == conversation_id,
                    QueuedMessage.steering_run_id == run_id,
                    QueuedMessage.steering_claimed.is_(False),
                )
                .order_by(QueuedMessage.position, QueuedMessage.id)
            )
        )
        if not ids:
            return []
        claimed = list(session.scalars(
            update(QueuedMessage)
            .where(QueuedMessage.id.in_(ids), QueuedMessage.steering_claimed.is_(False))
            .values(steering_claimed=True)
            .returning(QueuedMessage.id)
        ))
        session.commit()
        if not claimed:
            return []
        return list(session.scalars(select(QueuedMessage).where(QueuedMessage.id.in_(claimed))))


def acknowledge_steering(session_factory: sessionmaker[Session], run_id: int, item_ids: list[int]) -> None:
    """Consume steering rows after their text has been incorporated in a tool result."""
    if not item_ids:
        return
    with session_factory() as session:
        rows = list(session.scalars(select(QueuedMessage).where(QueuedMessage.id.in_(item_ids))))
        for row in rows:
            if row.steering_run_id == run_id:
                session.delete(row)
        session.commit()


def restore_steering(session_factory: sessionmaker[Session], run_id: int) -> None:
    """Return unacknowledged steering rows to the ordinary queue when the run ends."""
    with session_factory() as session:
        session.execute(
            update(QueuedMessage)
            .where(QueuedMessage.steering_run_id == run_id)
            .values(steering_run_id=None, steering_claimed=False)
        )
        session.commit()


def restore_interrupted_handoffs(session_factory: sessionmaker[Session]) -> None:
    """Make unfinished handoffs retryable after a server restart."""
    with session_factory() as session:
        session.execute(update(QueuedMessage).values(send_now_job_id=None, steering_run_id=None, steering_claimed=False))
        session.commit()


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
