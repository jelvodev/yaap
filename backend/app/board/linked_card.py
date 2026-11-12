"""The card a conversation belongs to, as the card tools see it.

The card is fixed when the run starts, from the conversation's link, so a tool never takes it as an argument: the
agent reads and changes only its own card, and of that only the specification and its open questions.
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.board.card import MAX_QUESTIONS, Card, Question, revise
from app.board.errors import InvalidCard
from app.board.links import card_of_conversation
from app.board.service import BoardService
from app.board.switch import board_enabled, board_tools_offered
from app.storage.models import Conversation

# What update_card may write: well under a card section's own limit, so a card file stays readable.
MAX_SPECIFICATION = 20_000
MAX_QUESTION = 1000


@dataclass(frozen=True)
class LinkedCard:
    board: BoardService
    project_id: int
    card_id: int

    def read(self) -> Card:
        """The card at the board's head. Raises a BoardError (broken, gone, board off)."""
        opened = self.board.opened(self.project_id)
        return self.board.read_card(opened.repository, self.card_id, opened.repository.head())

    def update(self, specification: str | None, question: str | None) -> Card:
        """Set the specification, append one open question, or both; nothing else. Raises InvalidCard or another
        BoardError."""
        checked_specification = checked_text(specification, "The specification", MAX_SPECIFICATION)
        checked_question = checked_text(question, "The question", MAX_QUESTION)
        if checked_specification is None and checked_question is None:
            raise InvalidCard("Give a specification, a question, or both.")
        opened = self.board.opened(self.project_id)
        written: list[Card] = []

        def change(card: Card) -> Card:
            now = self.board.clock()
            changes: dict[str, object] = {"updated": now}
            if checked_specification is not None:
                changes["specification"] = checked_specification
            if checked_question is not None:
                if len(card.questions) >= MAX_QUESTIONS:
                    raise InvalidCard(f"The card has {MAX_QUESTIONS} questions already.")
                changes["questions"] = [*card.questions, Question(asked=now, text=checked_question)]
            written[:] = [revise(card, changes)]
            return written[0]

        self.board.write_card(opened, self.card_id, change, {"changed": 1})
        return written[0]


def checked_text(value: str | None, name: str, limit: int) -> str | None:
    """The text without surrounding blank space, or None when not given; refused when empty or over its limit."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        raise InvalidCard(f"{name} is empty.")
    if len(text) > limit:
        raise InvalidCard(f"{name} is over {limit} characters.")
    return text


def linked_card(board: BoardService | None, session: Session, conversation: Conversation) -> LinkedCard | None:
    """The card the conversation belongs to, when the card tools are offered: a project conversation linked to a card
    while the project's board is on."""
    if board is None or conversation.project_id is None:
        return None
    card_id = card_of_conversation(session, conversation.id)
    enabled = board_enabled(session, conversation.environment_id, conversation.project_id).enabled
    if card_id is None or not board_tools_offered(enabled, True):
        return None
    return LinkedCard(board, conversation.project_id, card_id)
