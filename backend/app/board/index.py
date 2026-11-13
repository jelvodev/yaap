"""Keeping the board's index (`board_cards`, `board_versions`) in step with the files on the board's branch.

A sync reads only the files that changed since the commit the index matches (`board_state.head_commit`); when that
commit is gone (the branch was rewritten), every file whose blob differs from its row is read again. The rows are
derived from the files and nothing else writes them. A file that cannot be read is indexed as broken, with its error.
"""

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.board.card import BrokenCard, Card, parse_card
from app.board.models import BoardCardRow, BoardState, BoardVersionRow
from app.board.paths import classify
from app.board.repository import BoardRepository, FileContent, ReadError
from app.board.versions import BrokenVersion, parse_version

READ_ERRORS: dict[ReadError, str] = {
    "too_large": "The file is too large to read.",
    "not_text": "The file is not UTF-8 text.",
    "unreadable": "The file could not be read.",
}


@dataclass(frozen=True)
class SyncResult:
    head: str | None
    # How many files were read again; 0 when the index was already at the head.
    changed: int
    rebuilt: bool = False


def card_values(project_id: int, path: str, blob: str, parsed: Card | BrokenCard) -> dict[str, object]:
    """The index row's values for a card file."""
    if isinstance(parsed, BrokenCard):
        return {
            "project_id": project_id,
            "card_id": parsed.card_id or 0,
            "path": path,
            "title": "",
            "kind": None,
            "board_column": None,
            "version": None,
            "origin_type": None,
            "origin_ref": None,
            "waits_for": [],
            "open_questions": 0,
            "has_specification": False,
            "cost_usd": 0.0,
            "plan_id": None,
            "review_id": None,
            "blob_sha": blob,
            "broken": True,
            "error": parsed.error,
            "updated_at": None,
        }
    return {
        "project_id": project_id,
        "card_id": parsed.id,
        "path": path,
        "title": parsed.title,
        "kind": parsed.kind,
        "board_column": parsed.column,
        "version": parsed.version,
        "origin_type": parsed.origin.type,
        "origin_ref": parsed.origin.ref,
        "waits_for": list(parsed.waits_for),
        "open_questions": sum(1 for question in parsed.questions if question.answer is None),
        "has_specification": bool(parsed.specification.strip()),
        "cost_usd": parsed.cost_usd,
        "plan_id": parsed.links.plan_id,
        "review_id": parsed.links.review_id,
        "blob_sha": blob,
        "broken": False,
        "error": None,
        "updated_at": parsed.updated,
    }


def parse_file_card(content: FileContent) -> Card | BrokenCard:
    if content.text is None:
        found = classify(content.path)
        card_id = found.card_id if found is not None else None
        return BrokenCard(content.path, card_id, READ_ERRORS[content.error or "unreadable"])
    return parse_card(content.text, content.path)


def version_values(project_id: int, name: str, content: FileContent) -> dict[str, object]:
    parsed = parse_version(content.text, content.path) if content.text is not None else None
    if parsed is None or isinstance(parsed, BrokenVersion):
        return {"project_id": project_id, "name": name, "state": None, "budget_usd": None, "blob_sha": content.blob}
    return {
        "project_id": project_id,
        "name": name,
        "state": parsed.state,
        "budget_usd": parsed.budget_usd,
        "blob_sha": content.blob,
    }


@dataclass
class BoardIndexer:
    """Syncs a project's index rows with its board repository. One sync per project at a time."""

    session_factory: sessionmaker[Session]
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)

    def __post_init__(self) -> None:
        self._locks: dict[int, threading.Lock] = {}
        self._guard = threading.Lock()

    def lock(self, project_id: int) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(project_id, threading.Lock())

    def sync(self, project_id: int, repository: BoardRepository) -> SyncResult:
        """Bring the index to the branch's head. Without a board state (no board started) nothing is indexed."""
        with self.lock(project_id):
            head = repository.head()
            with self.session_factory() as session:
                state = session.get(BoardState, project_id)
                if state is None or head is None or state.head_commit == head:
                    return SyncResult(head, 0)
                old = state.head_commit
                if old is not None and repository.has_commit(old):
                    result = self.read_paths(session, project_id, repository, head, repository.changed_paths(old, head))
                else:
                    result = self.rebuild_rows(session, project_id, repository, head)
                self.finish(session, state, head)
                session.commit()
                return result

    def rebuild(self, project_id: int, repository: BoardRepository) -> SyncResult:
        """Read every board file again, whatever the index holds."""
        with self.lock(project_id):
            head = repository.head()
            with self.session_factory() as session:
                state = session.get(BoardState, project_id)
                if state is None or head is None:
                    return SyncResult(head, 0)
                session.execute(delete(BoardCardRow).where(BoardCardRow.project_id == project_id))
                session.execute(delete(BoardVersionRow).where(BoardVersionRow.project_id == project_id))
                result = self.rebuild_rows(session, project_id, repository, head)
                self.finish(session, state, head)
                session.commit()
                return result

    def rebuild_rows(self, session: Session, project_id: int, repository: BoardRepository, head: str) -> SyncResult:
        """Rows for every file at the head: files whose blob is unchanged are not read; rows without a file go."""
        files = {entry.path: entry.blob for entry in repository.list_files(head)}
        known = self.known_blobs(session, project_id)
        changed = [path for path, blob in files.items() if known.get(path) != blob]
        gone = [path for path in known if path not in files]
        result = self.read_paths(session, project_id, repository, head, [*changed, *gone])
        return SyncResult(head, result.changed, rebuilt=True)

    @staticmethod
    def known_blobs(session: Session, project_id: int) -> dict[str, str]:
        cards = session.execute(
            select(BoardCardRow.path, BoardCardRow.blob_sha).where(BoardCardRow.project_id == project_id)
        )
        versions = session.execute(
            select(BoardVersionRow.name, BoardVersionRow.blob_sha).where(BoardVersionRow.project_id == project_id)
        )
        found = {path: blob for path, blob in cards}
        found.update({f"versions/{name}.md": blob for name, blob in versions})
        return found

    def read_paths(
        self, session: Session, project_id: int, repository: BoardRepository, head: str, paths: list[str]
    ) -> SyncResult:
        contents = repository.read_many(paths, head) if paths else {}
        for path in paths:
            found = classify(path)
            if found is None:
                continue
            content = contents.get(path)
            if found.kind == "card" and found.card_id is not None:
                self.put_card(session, project_id, found.card_id, path, content)
            elif found.version is not None:
                self.put_version(session, project_id, found.version, content)
        return SyncResult(head, len(paths))

    @staticmethod
    def put_card(session: Session, project_id: int, card_id: int, path: str, content: FileContent | None) -> None:
        row = session.scalar(
            select(BoardCardRow).where(BoardCardRow.project_id == project_id, BoardCardRow.card_id == card_id)
        )
        if content is None:
            if row is not None:
                session.delete(row)
            return
        values = card_values(project_id, path, content.blob, parse_file_card(content))
        values["card_id"] = card_id
        apply_values(session, row, BoardCardRow, values)

    @staticmethod
    def put_version(session: Session, project_id: int, name: str, content: FileContent | None) -> None:
        row = session.scalar(
            select(BoardVersionRow).where(BoardVersionRow.project_id == project_id, BoardVersionRow.name == name)
        )
        if content is None:
            if row is not None:
                session.delete(row)
            return
        apply_values(session, row, BoardVersionRow, version_values(project_id, name, content))

    def finish(self, session: Session, state: BoardState, head: str) -> None:
        """Record the head the index matches; the next id stays above every card id ever seen."""
        highest = session.scalar(
            select(BoardCardRow.card_id)
            .where(BoardCardRow.project_id == state.project_id)
            .order_by(BoardCardRow.card_id.desc())
            .limit(1)
        )
        state.next_card_id = max(state.next_card_id, (highest or 0) + 1)
        state.head_commit = head
        state.synced_at = self.clock()


def apply_values(
    session: Session,
    row: BoardCardRow | BoardVersionRow | None,
    model: type[BoardCardRow] | type[BoardVersionRow],
    values: Mapping[str, object],
) -> None:
    if row is None:
        session.add(model(**values))
        session.flush()
        return
    for key, value in values.items():
        setattr(row, key, value)
