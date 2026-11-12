"""Cards that come in from outside the user's hand: the agent's (`add_card`, `add_to_card`), a review's finding and
imported issues. They land in Inbox, name their origin (filled by YAAP, never by the agent) and stay within limits.

Before the agent adds a card, the board is searched for similar cards (`app.board.similarity`); when there are, the
agent is shown them and may add its note to one of them instead. The limits and the duplicate decision are pure
functions; the service around them reads the index and writes through the board service.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.board.card import Card, Kind, Origin, OriginType, revise
from app.board.errors import AlreadyOnBoard, FindingNotFound, IntakeLimit, InvalidCard
from app.board.index import parse_file_card
from app.board.models import BoardCardRow, BoardState
from app.board.operations import NewCard
from app.board.paths import card_path
from app.board.repository import BoardRepository
from app.board.service import BoardService
from app.board.similarity import CardText, most_similar, title_score
from app.board.stage_text import escape_headings
from app.board.switch import board_enabled
from app.board.views import CardDetail
from app.plans.markdown import code_span
from app.storage.models import Conversation, Review

MAX_PER_REPLY = 5
MAX_PER_CONVERSATION = 20
MAX_OPEN_INBOX = 200
# Similar cards shown to the agent, and how many cards' descriptions are read to find them.
MAX_SIMILAR = 5
MAX_READ_FOR_SIMILAR = 20
MAX_DESCRIPTION = 8000
MAX_WHY = 2000
MAX_NOTE = 2000
NOTICED_AGAIN = "Noticed again"


# --- Limits and origins (pure) -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class IntakeCounts:
    """Cards already added by this reply and this conversation, and the open cards in Inbox."""

    reply: int
    conversation: int
    inbox: int


def limit_refusal(counts: IntakeCounts, adding: int = 1, from_agent: bool = True) -> str | None:
    """Why `adding` more cards may not be added now, or None when they may. The reply and conversation limits are
    the agent's; the Inbox limit holds for every card that comes in from outside."""
    if from_agent and counts.reply + adding > MAX_PER_REPLY:
        return f"At most {MAX_PER_REPLY} cards can be added in one reply."
    if from_agent and counts.conversation + adding > MAX_PER_CONVERSATION:
        return f"At most {MAX_PER_CONVERSATION} cards can be added from one conversation."
    if counts.inbox + adding > MAX_OPEN_INBOX:
        return f"Inbox has {counts.inbox} cards; at most {MAX_OPEN_INBOX} can wait there. Accept or drop some first."
    return None


@dataclass(frozen=True)
class AgentOrigin:
    """Where an agent's card comes from: its conversation and the run of the reply. Set by YAAP from the run."""

    conversation_id: int
    run_id: int | None

    @property
    def conversation_ref(self) -> str:
        return f"conversation:{self.conversation_id}"

    @property
    def ref(self) -> str:
        return self.conversation_ref if self.run_id is None else f"{self.conversation_ref}/run:{self.run_id}"


def finding_ref(review_id: int, index: int) -> str:
    return f"review:{review_id}/finding:{index}"


def agent_description(description: str, why: str) -> str:
    """The card's description: what the agent describes, then why it noticed it; card headings moved in."""
    parts = [escape_headings(description)]
    if why.strip():
        parts.append(f"**Why the agent noticed it:** {escape_headings(why)}")
    return "\n\n".join(part for part in parts if part)


def noticed_again(card: Card, note: str, where: str, now: datetime) -> Card:
    """The card with the note appended to its description under "Noticed again"."""
    line = f"**{NOTICED_AGAIN}** ({now.strftime('%Y-%m-%d %H:%M')} UTC, {where}): {escape_headings(note)}"
    description = f"{card.description.rstrip()}\n\n{line}" if card.description.strip() else line
    return revise(card, {"description": description, "updated": now})


def finding_card(review_id: int, index: int, finding: dict[str, object]) -> NewCard:
    """A card in Inbox for one finding of a review: its file, line, severity and explanation."""
    path = str(finding.get("path") or "")
    line = finding.get("line")
    severity = str(finding.get("severity") or "")
    explanation = str(finding.get("explanation") or "").strip()
    first = explanation.split("\n", 1)[0].strip()
    title = f"{path}: {first}" if path and first else first or path or f"Finding of review #{review_id}"
    title = title if len(title) <= 200 else title[:199].rstrip() + "…"
    where = code_span(path) + (f", line {line}" if isinstance(line, int) else "") if path else "no file"
    description = "\n\n".join(
        part
        for part in (
            f"A finding of review #{review_id} that the change did not fix.",
            f"- File: {where}\n- Severity: {severity or 'not given'}",
            escape_headings(explanation),
        )
        if part
    )
    origin = Origin(type="review", ref=finding_ref(review_id, index))
    return NewCard(title=title, kind="bug", column="inbox", description=description, origin=origin)


# --- Results -------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SimilarCard:
    card_id: int
    title: str
    column: str
    score: float


@dataclass(frozen=True)
class Added:
    card: CardDetail


@dataclass(frozen=True)
class Similar:
    cards: tuple[SimilarCard, ...]


# --- The service ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CardIntake:
    board: BoardService

    def counts(self, session: Session, project_id: int, origin: AgentOrigin | None) -> IntakeCounts:
        rows = select(func.count()).select_from(BoardCardRow).where(BoardCardRow.project_id == project_id)
        inbox = session.scalar(rows.where(BoardCardRow.board_column == "inbox", BoardCardRow.broken.is_(False))) or 0
        if origin is None:
            return IntakeCounts(0, 0, inbox)
        agent_rows = rows.where(BoardCardRow.origin_type == "agent")
        reply = session.scalar(agent_rows.where(BoardCardRow.origin_ref == origin.ref)) or 0
        conversation = (
            session.scalar(
                agent_rows.where(
                    or_(
                        BoardCardRow.origin_ref == origin.conversation_ref,
                        BoardCardRow.origin_ref.startswith(f"{origin.conversation_ref}/", autoescape=True),
                    )
                )
            )
            or 0
        )
        return IntakeCounts(reply, conversation, inbox)

    def room_in_inbox(self, session: Session, project_id: int) -> int:
        """How many more cards from outside may land in Inbox now."""
        return MAX_OPEN_INBOX - self.counts(session, project_id, None).inbox

    def check_limits(self, project_id: int, origin: AgentOrigin | None, adding: int = 1) -> None:
        with self.board.session_factory() as session:
            counts = self.counts(session, project_id, origin)
        refusal = limit_refusal(counts, adding, from_agent=origin is not None)
        if refusal is not None:
            raise IntakeLimit(refusal)

    def similar(self, project_id: int, new: CardText) -> tuple[SimilarCard, ...]:
        """Cards on the board like the new one: titles from the index, descriptions of the likeliest read from the
        files."""
        opened = self.board.opened(project_id)
        with self.board.session_factory() as session:
            rows = list(
                session.execute(
                    select(BoardCardRow.card_id, BoardCardRow.title, BoardCardRow.board_column).where(
                        BoardCardRow.project_id == project_id, BoardCardRow.broken.is_(False)
                    )
                )
            )
        known = {card_id: (title, column or "") for card_id, title, column in rows}
        likeliest = sorted(known, key=lambda card_id: -title_score(new, known[card_id][0]))[:MAX_READ_FOR_SIMILAR]
        descriptions = self.descriptions(opened.repository, likeliest)
        candidates = [
            (card_id, CardText(title, descriptions.get(card_id, ""))) for card_id, (title, _) in known.items()
        ]
        return tuple(
            SimilarCard(card_id, known[card_id][0], known[card_id][1], round(score, 2))
            for card_id, score in most_similar(new, candidates, MAX_SIMILAR)
        )

    @staticmethod
    def descriptions(repository: BoardRepository, card_ids: Sequence[int]) -> dict[int, str]:
        found = repository.read_many([card_path(card_id) for card_id in card_ids])
        texts: dict[int, str] = {}
        for card_id in card_ids:
            content = found.get(card_path(card_id))
            parsed = parse_file_card(content) if content is not None else None
            if isinstance(parsed, Card):
                texts[card_id] = parsed.description
        return texts

    def add_from_agent(
        self,
        project_id: int,
        origin: AgentOrigin,
        title: str,
        kind: Kind,
        description: str,
        why: str,
        confirmed_new: bool,
    ) -> Added | Similar:
        """The agent's card in Inbox, unless similar cards exist and the agent has not seen them yet."""
        if len(description) > MAX_DESCRIPTION:
            raise InvalidCard(f"The description is over {MAX_DESCRIPTION} characters.")
        if len(why) > MAX_WHY:
            raise InvalidCard(f"'why' is over {MAX_WHY} characters.")
        self.check_limits(project_id, origin)
        if not confirmed_new:
            similar = self.similar(project_id, CardText(title, description))
            if similar:
                return Similar(similar)
        request = NewCard(
            title=title,
            kind=kind,
            column="inbox",
            description=agent_description(description, why),
            origin=Origin(type="agent", ref=origin.ref),
        )
        return Added(self.board.create_card(project_id, request))

    def add_note(self, project_id: int, card_id: int, note: str, where: str) -> Card:
        """Append the agent's note to a card under "Noticed again"; nothing else on the card changes."""
        text = note.strip()
        if not text:
            raise InvalidCard("The note is empty.")
        if len(text) > MAX_NOTE:
            raise InvalidCard(f"The note is over {MAX_NOTE} characters.")
        opened = self.board.opened(project_id)
        written: list[Card] = []

        def change(card: Card) -> Card:
            written[:] = [noticed_again(card, text, where, self.board.clock())]
            return written[0]

        self.board.write_card(opened, card_id, change, {"changed": 1})
        return written[0]

    def existing_card(self, project_id: int, origin_type: OriginType, ref: str) -> int | None:
        """The card that already came from this origin, if any."""
        with self.board.session_factory() as session:
            return session.scalar(
                select(BoardCardRow.card_id).where(
                    BoardCardRow.project_id == project_id,
                    BoardCardRow.origin_type == origin_type,
                    BoardCardRow.origin_ref == ref,
                )
            )

    def add_from_finding(self, project_id: int, review_id: int, index: int) -> CardDetail:
        """*Make a card* on a review's finding: a card in Inbox with origin `review`. Raises FindingNotFound,
        AlreadyOnBoard or IntakeLimit."""
        with self.board.session_factory() as session:
            review = session.get(Review, review_id)
            findings = review.findings if review is not None and review.project_id == project_id else None
        if findings is None or not 0 <= index < len(findings) or not isinstance(findings[index], dict):
            raise FindingNotFound()
        self.board.opened(project_id)  # the board is on and started, and the index synced, before it is searched
        existing = self.existing_card(project_id, "review", finding_ref(review_id, index))
        if existing is not None:
            raise AlreadyOnBoard(existing)
        self.check_limits(project_id, None)
        return self.board.create_card(project_id, finding_card(review_id, index, findings[index]))


def intake_for(board: BoardService | None, session: Session, conversation: Conversation) -> CardIntake | None:
    """The intake for a run, when `add_card` is offered: a project conversation while its board is on and started."""
    if board is None or conversation.project_id is None:
        return None
    if not board_enabled(session, conversation.environment_id, conversation.project_id).enabled:
        return None
    if session.get(BoardState, conversation.project_id) is None:
        return None
    return CardIntake(board)
