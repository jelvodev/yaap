"""Reading what the board holds before a write or an answer: one card with its links, the next free card id and the
version file a change builds on."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.board.card import BrokenCard, Card, parse_checklist
from app.board.errors import BoardFull, CardNotFound, InvalidVersion
from app.board.index import parse_file_card
from app.board.links import conversations_of
from app.board.models import BoardCardRow, BoardState
from app.board.operations import MAX_CARDS, MAX_VERSIONS
from app.board.paths import card_path
from app.board.planning import Opened
from app.board.repository import BoardRepository
from app.board.storage import BoardStorages
from app.board.versions import Version, parse_version
from app.board.views import CardDetail, LinkedConversation, Related, card_view


def card_detail(
    session_factory: sessionmaker[Session], storage: BoardStorages, opened: Opened, card_id: int
) -> CardDetail:
    project_id = opened.project.id
    with session_factory() as session:
        row = session.scalar(
            select(BoardCardRow).where(BoardCardRow.project_id == project_id, BoardCardRow.card_id == card_id)
        )
        if row is None:
            raise CardNotFound(card_id)
        related = Related.read(session, project_id, [row])
        linked = conversations_of(session, project_id, card_id)
        conversations = tuple(LinkedConversation(*found) for found in linked)
    view = card_view(row, related)
    content = opened.repository.read(card_path(card_id))
    parsed = parse_file_card(content) if content is not None else None
    card = parsed if isinstance(parsed, Card) else None
    broken_text = parsed.text if isinstance(parsed, BrokenCard) else None
    items = tuple(parse_checklist(card.definition_of_done)) if card is not None else ()
    location = storage.describe(opened.project).location
    return CardDetail(view, card, broken_text, items, conversations, location)


def free_card_id(
    session_factory: sessionmaker[Session], repository: BoardRepository, project_id: int, head: str | None
) -> int:
    """The id for a new card: past the board's counter and past every card file on the branch."""
    with session_factory() as session:
        count = session.scalar(
            select(func.count()).select_from(BoardCardRow).where(BoardCardRow.project_id == project_id)
        )
        state = session.get(BoardState, project_id)
        next_id = state.next_card_id if state is not None else 1
    if (count or 0) >= MAX_CARDS:
        raise BoardFull(f"A board holds at most {MAX_CARDS} cards.")
    on_branch = [entry.path for entry in repository.list_files(head) if entry.path.startswith("cards/")]
    highest = max((int(path.removeprefix("cards/").removesuffix(".md")) for path in on_branch), default=0)
    return max(next_id, highest + 1)


def existing_version(repository: BoardRepository, path: str, head: str | None) -> Version | None:
    """The version file as it is at the head, None when there is none yet (and room for one more)."""
    content = repository.read(path, head) if head is not None else None
    if content is not None:
        parsed = parse_version(content.text, path) if content.text is not None else None
        if not isinstance(parsed, Version):
            raise InvalidVersion("The version's file cannot be read; fix it on the board's branch first.")
        return parsed
    versions = [entry for entry in repository.list_files(head) if entry.path.startswith("versions/")]
    if len(versions) >= MAX_VERSIONS:
        raise BoardFull(f"A board holds at most {MAX_VERSIONS} versions.")
    return None
