"""The card and version changes the service writes, as pure functions of what was read: a new card, a patch, a
move, an answer, a send back. Each returns the new card or version, checked against the file format's rules."""

from collections.abc import Mapping, Set
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from app.board.card import Card, Kind, Origin, Question, revise, validate_card
from app.board.errors import AlreadyAnswered, InvalidCard, InvalidVersion
from app.board.flow import SENT_BACK_HEADING
from app.board.paths import is_version_name
from app.board.versions import Version, validate_version

# What a PATCH may change; column, id, origin, cost, links and created are set only by moves and the service.
PATCHABLE = frozenset({"title", "kind", "version", "description", "specification", "definition_of_done", "waits_for"})
VERSION_FIELDS = frozenset({"state", "budget_usd", "notes", "acceptance_criteria"})
MAX_CARDS = 2000
MAX_VERSIONS = 100
MAX_ANSWER = 4000

NewColumn = Literal["inbox", "idea"]


@dataclass(frozen=True)
class NewCard:
    title: str
    kind: Kind = "feature"
    column: NewColumn = "idea"
    version: str | None = None
    description: str = ""
    origin: Origin = field(default_factory=Origin)


def new_card(card_id: int, request: NewCard, definition_of_done: str, now: datetime) -> Card:
    return validate_card(
        {
            "id": card_id,
            "title": request.title,
            "kind": request.kind,
            "column": request.column,
            "version": request.version,
            "origin": request.origin,
            "created": now,
            "updated": now,
            "description": request.description,
            "definition_of_done": definition_of_done,
        }
    )


def patched(card: Card, patch: Mapping[str, object], known_cards: Set[int], now: datetime) -> Card:
    """The card with the patch applied. Fields a patch may not change are refused, as are waits for unknown cards."""
    refused = sorted(set(patch) - PATCHABLE)
    if refused:
        raise InvalidCard(f"These fields cannot be changed here: {', '.join(refused)}.")
    waits = patch.get("waits_for")
    if isinstance(waits, list):
        unknown = [str(card_id) for card_id in waits if card_id not in known_cards and card_id != card.id]
        if unknown:
            raise InvalidCard(f"waits_for names cards that do not exist: {', '.join(unknown)}.")
    return revise(card, {**patch, "updated": now})


def moved(card: Card, column: str, now: datetime) -> Card:
    return revise(card, {"column": column, "updated": now})


def answered(card: Card, index: int, text: str, now: datetime) -> Card:
    if not 0 <= index < len(card.questions):
        raise InvalidCard(f"The card has no question {index}.")
    answer = text.strip()
    if not 1 <= len(answer) <= MAX_ANSWER:
        raise InvalidCard(f"An answer has 1 to {MAX_ANSWER} characters.")
    question = card.questions[index]
    if question.answer is not None:
        raise AlreadyAnswered()
    questions = list(card.questions)
    questions[index] = Question(asked=question.asked, text=question.text, answer=answer)
    return revise(card, {"questions": questions, "updated": now})


def sent_back(card: Card, reason: str, now: datetime) -> Card:
    """The card back in Design with the reason under "Sent back" in its description."""
    note = f"**{SENT_BACK_HEADING}** ({now.strftime('%Y-%m-%d %H:%M')} UTC): {reason.strip() or 'no reason given'}"
    description = f"{card.description.rstrip()}\n\n{note}" if card.description.strip() else note
    return revise(card, {"column": "design", "description": description, "updated": now})


def checked_version_name(name: str) -> str:
    if not is_version_name(name):
        raise InvalidVersion("A version name has 1 to 40 letters, digits, dots, dashes or underscores.")
    return name


def put_version(existing: Version | None, name: str, changes: Mapping[str, object], now: datetime) -> Version:
    """A new version or the existing one with the changes; raises InvalidVersion."""
    refused = sorted(set(changes) - VERSION_FIELDS)
    if refused:
        raise InvalidVersion(f"These fields cannot be set: {', '.join(refused)}.")
    base: dict[str, object] = existing.model_dump() if existing is not None else {"name": name, "created": now}
    try:
        return validate_version({**base, **changes})
    except InvalidCard as error:
        raise InvalidVersion(str(error)) from error
