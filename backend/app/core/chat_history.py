"""Reading and storing a conversation's messages: the branch, the model's history and the user's message."""

from collections.abc import Sequence
from datetime import UTC, datetime
from types import EllipsisType
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.attachments.service import AttachmentResolver, attachment_block
from app.core.message_parts import model_content
from app.imports.sessions import imported_model_content, is_imported
from app.providers.base import HistoryMessage
from app.storage.models import Attachment, Conversation, Message


DEFAULT_TITLE = "New conversation"
TITLE_LENGTH = 60


def load_branch(session: Session, conversation_id: int, head_message_id: int | None) -> list[Message]:
    """Messages from the root to the given head, following parent links."""
    if head_message_id is None:
        return []
    messages = {
        message.id: message
        for message in session.scalars(select(Message).where(Message.conversation_id == conversation_id))
    }
    branch: list[Message] = []
    current = messages.get(head_message_id)
    while current is not None:
        branch.append(current)
        current = messages.get(current.parent_id) if current.parent_id is not None else None
    branch.reverse()
    return branch


def is_part_of_history(message: Message) -> bool:
    """Only turns the model actually completed are sent back; cancelled, failed and refused replies are not."""
    if message.role == "user":
        return True
    return message.provider_raw is not None and message.status != "refused"


def build_history(branch: list[Message], resolver: AttachmentResolver | None = None) -> list[HistoryMessage]:
    """The branch as model input: commands filled in, skills as text, attachments as images, documents or text."""
    history = []
    for message in branch:
        if not is_part_of_history(message):
            continue
        content = message.content
        if message.role == "user":
            content = model_content(content)
            if resolver is not None:
                content = resolver.expand(content)
        elif is_imported(message.provider_raw):
            # A turn of an imported Claude Code or Codex session: its tool calls go along as text.
            content = imported_model_content(content, message.provider_raw[0].get("tool", ""))  # type: ignore[index]  # is_imported() has checked provider_raw is not empty
        history.append(HistoryMessage(role=message.role, content=content, provider_raw=message.provider_raw))  # type: ignore[arg-type]  # only user and assistant rows pass is_part_of_history
    return history


def make_title(text: str) -> str:
    first_line = " ".join(text.split())
    return first_line if len(first_line) <= TITLE_LENGTH else first_line[: TITLE_LENGTH - 1].rstrip() + "…"


def add_user_message(
    session: Session,
    conversation: Conversation,
    text: str,
    attachments: Sequence[Attachment] = (),
    extra_blocks: Sequence[dict[str, Any]] = (),
    parent_id: int | None | EllipsisType = ...,
) -> Message:
    """Store the user's message: skills first, then the typed text, its command expansion and attachments.

    It follows the current head, unless parent_id says where it goes (a new version of an earlier message).
    """
    now = datetime.now(UTC)
    skills = [block for block in extra_blocks if block["type"] in ("skill", "skill_removed")]
    commands = [block for block in extra_blocks if block["type"] in ("command", "goal")]
    content: list[dict[str, Any]] = [*skills]
    content += [{"type": "text", "text": text}] if text else []
    content += commands
    content += [attachment_block(attachment) for attachment in attachments]
    message = Message(
        conversation_id=conversation.id,
        parent_id=conversation.head_message_id if parent_id is ... else parent_id,
        role="user",
        content=content,
        status="complete",
        created_at=now,
    )
    session.add(message)
    session.flush()
    for attachment in attachments:
        # A new version of a message shares its files; they stay with the message they came with.
        if attachment.message_id is None:
            attachment.message_id = message.id
    conversation.head_message_id = message.id
    conversation.updated_at = now
    if conversation.title == DEFAULT_TITLE:
        conversation.title = make_title(text or ", ".join(attachment.filename for attachment in attachments))
    session.commit()
    return message
