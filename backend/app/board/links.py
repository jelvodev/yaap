"""What belongs to a card: its conversations (with their role), its plan, its review and its branch.

The card file's `links` hold the ids, so they travel with the board; `board_card_conversations` answers the other
way round (which card a conversation belongs to) and the role. The revisions here are pure; the service writes them.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.board.card import MAX_CONVERSATIONS, Card, revise
from app.board.models import BoardCardConversation, ConversationRole
from app.board.switch import board_enabled
from app.storage.models import Conversation


def with_conversation(card: Card, conversation_id: int) -> Card:
    """The card with the conversation in its links (the oldest dropped when the list is full)."""
    conversations = [known for known in card.links.conversations if known != conversation_id]
    conversations = [*conversations, conversation_id][-MAX_CONVERSATIONS:]
    return revise(card, {"links": {**card.links.model_dump(), "conversations": conversations}})


def with_plan(card: Card, plan_id: int | None) -> Card:
    return revise(card, {"links": {**card.links.model_dump(), "plan_id": plan_id}})


def with_review(card: Card, review_id: int | None) -> Card:
    return revise(card, {"links": {**card.links.model_dump(), "review_id": review_id}})


def with_branch(card: Card, branch: str | None) -> Card:
    return revise(card, {"links": {**card.links.model_dump(), "branch": branch}})


def record_conversation(
    session: Session, project_id: int, card_id: int, conversation_id: int, role: ConversationRole
) -> None:
    """Link a conversation to a card in the index; a conversation belongs to one card, so a new link replaces an
    old one. The caller commits."""
    session.execute(delete(BoardCardConversation).where(BoardCardConversation.conversation_id == conversation_id))
    session.add(
        BoardCardConversation(
            project_id=project_id,
            card_id=card_id,
            conversation_id=conversation_id,
            role=role,
            created_at=datetime.now(UTC),
        )
    )


def forget_card(session: Session, project_id: int, card_id: int) -> None:
    """Drop the links of a card that is gone. The caller commits."""
    session.execute(
        delete(BoardCardConversation).where(
            BoardCardConversation.project_id == project_id, BoardCardConversation.card_id == card_id
        )
    )


def card_of_conversation(session: Session, conversation_id: int) -> int | None:
    return session.scalar(
        select(BoardCardConversation.card_id).where(BoardCardConversation.conversation_id == conversation_id)
    )


def visible_card(session: Session, conversation: Conversation) -> int | None:
    """The card a conversation belongs to, while its project's board is on (off, no card chip is shown)."""
    if conversation.project_id is None:
        return None
    card_id = card_of_conversation(session, conversation.id)
    if card_id is None or not board_enabled(session, conversation.environment_id, conversation.project_id).enabled:
        return None
    return card_id


def conversations_of(session: Session, project_id: int, card_id: int) -> list[tuple[int, str, ConversationRole]]:
    """(id, title, role) of the card's conversations that still exist, oldest first."""
    rows = session.execute(
        select(Conversation.id, Conversation.title, BoardCardConversation.role)
        .join(Conversation, Conversation.id == BoardCardConversation.conversation_id)
        .where(BoardCardConversation.project_id == project_id, BoardCardConversation.card_id == card_id)
        .order_by(BoardCardConversation.id)
    )
    return [(conversation_id, title or "", role) for conversation_id, title, role in rows]


def conversation_ids_by_card(session: Session, project_id: int) -> dict[int, list[int]]:
    """The linked conversation ids per card of a project."""
    found: dict[int, list[int]] = {}
    rows = session.execute(
        select(BoardCardConversation.card_id, BoardCardConversation.conversation_id).where(
            BoardCardConversation.project_id == project_id
        )
    )
    for card_id, conversation_id in rows:
        found.setdefault(card_id, []).append(conversation_id)
    return found


def linked_ids(card: Card, recorded: Sequence[int]) -> list[int]:
    """The card's conversations from its file and from the index, each once."""
    return sorted({*card.links.conversations, *recorded})
