"""The board's index follows the files on the board's branch: incremental syncs, rebuilds, broken files, ids."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from app.board.index import BoardIndexer, SyncResult
from app.board.models import BoardCardRow, BoardState, BoardVersionRow
from app.board.paths import MAX_FILE_BYTES
from app.board.repository import BoardRepository, Delete, Put
from tests.board_support import sessions_in
from tests.test_board_repository import REF, git, plumb_commit

PROJECT = 1
NOW = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)


def card(number: int, title: str = "A card", **extra: str) -> str:
    fields = {"id": str(number), "title": title, "created": "2026-11-01T10:00:00Z", "updated": "2026-11-02T10:00:00Z"}
    fields.update(extra)
    front = "".join(f"{key}: {value}\n" for key, value in fields.items())
    return f"---\n{front}---\nBody\n\n## Specification\n\nSpec\n"


VERSION = "---\nname: 0.8.0\nstate: open\nbudget_usd: 12\ncreated: 2026-11-01T10:00:00Z\n---\nNotes\n"


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    git(project, "init", "-q", "-b", "main")
    (project / "a.txt").write_text("a\n", encoding="utf-8")
    git(project, "add", ".")
    git(project, "commit", "-q", "-m", "first")
    return project


@pytest.fixture
def repository(folder: Path) -> BoardRepository:
    return BoardRepository(folder)


@pytest.fixture
def sessions(tmp_path: Path):
    return sessions_in(tmp_path)


@pytest.fixture
def indexer(sessions) -> BoardIndexer:
    return BoardIndexer(sessions, clock=lambda: NOW)


def start_board(sessions, next_card_id: int = 1) -> None:
    with sessions() as session:
        session.add(BoardState(project_id=PROJECT, storage="branch", next_card_id=next_card_id))
        session.commit()


def cards(sessions) -> dict[int, BoardCardRow]:
    with sessions() as session:
        query = select(BoardCardRow).where(BoardCardRow.project_id == PROJECT)
        return {row.card_id: row for row in session.scalars(query)}


def state(sessions) -> BoardState:
    with sessions() as session:
        found = session.get(BoardState, PROJECT)
        assert found is not None
        return found


def test_nothing_is_indexed_without_a_board_state_or_a_branch(indexer, repository, sessions) -> None:
    assert indexer.sync(PROJECT, repository) == SyncResult(None, 0)
    repository.commit_changes([Put("cards/1.md", card(1))], "m", None)
    assert indexer.sync(PROJECT, repository).changed == 0 and cards(sessions) == {}
    start_board(sessions)
    assert indexer.sync(PROJECT, repository).head is not None
    assert list(cards(sessions)) == [1]


def test_the_first_sync_indexes_every_card_and_version_with_their_fields(indexer, repository, sessions) -> None:
    start_board(sessions)
    text = card(
        3,
        "Fix it",
        kind="bug",
        column="plan",
        version="0.8.0",
        waits_for="[1, 2]",
        cost_usd="2.5",
        links="{plan_id: 4, review_id: 6}",
        origin="{type: chat, ref: c1}",
        questions="[{asked: 2026-11-02T08:00:00Z, text: 'Q1', answer: null}, "
        "{asked: 2026-11-02T08:00:00Z, text: 'Q2', answer: 'A'}]",
    )
    written = repository.commit_changes([Put("cards/3.md", text), Put("versions/0.8.0.md", VERSION)], "m", None)
    result = indexer.sync(PROJECT, repository)
    assert result.head == written.commit and result.changed == 2
    row = cards(sessions)[3]
    assert (row.title, row.kind, row.board_column) == ("Fix it", "bug", "plan")
    assert (row.version, row.waits_for) == ("0.8.0", [1, 2])
    assert (row.open_questions, row.has_specification, row.cost_usd, row.plan_id, row.review_id) == (1, True, 2.5, 4, 6)
    assert (row.origin_type, row.origin_ref, row.broken, row.error) == ("chat", "c1", False, None)
    assert row.path == "cards/3.md" and row.updated_at == datetime(2026, 11, 2, 10, tzinfo=UTC)
    with sessions() as session:
        version = session.scalars(select(BoardVersionRow)).one()
    assert (version.name, version.state, version.budget_usd) == ("0.8.0", "open", 12.0)
    after = state(sessions)
    assert after.head_commit == written.commit and after.next_card_id == 4 and after.synced_at is not None


def test_a_sync_at_the_same_head_reads_nothing(indexer, repository, sessions) -> None:
    start_board(sessions)
    repository.commit_changes([Put("cards/1.md", card(1))], "m", None)
    indexer.sync(PROJECT, repository)
    assert indexer.sync(PROJECT, repository).changed == 0


def test_a_later_sync_reads_only_the_files_that_changed(indexer, repository, sessions, monkeypatch) -> None:
    start_board(sessions)
    first = repository.commit_changes([Put("cards/1.md", card(1)), Put("cards/2.md", card(2))], "m", None)
    indexer.sync(PROJECT, repository)
    second = repository.commit_changes(
        [Put("cards/2.md", card(2, "Renamed", column="idea")), Put("cards/3.md", card(3)), Delete("cards/1.md")],
        "m",
        first.commit,
    )
    seen: list[list[str]] = []
    real = repository.read_many

    def recording(paths, commit=None):
        seen.append(sorted(paths))
        return real(paths, commit)

    monkeypatch.setattr(repository, "read_many", recording)
    result = indexer.sync(PROJECT, repository)
    assert result == SyncResult(second.commit, 3) and not result.rebuilt
    assert seen == [["cards/1.md", "cards/2.md", "cards/3.md"]]
    found = cards(sessions)
    assert sorted(found) == [2, 3] and found[2].title == "Renamed" and found[2].board_column == "idea"


def test_a_hand_edit_on_the_branch_shows_after_a_sync(indexer, repository, sessions, folder) -> None:
    start_board(sessions)
    first = repository.commit_changes([Put("cards/1.md", card(1))], "m", None)
    indexer.sync(PROJECT, repository)
    # A different tree with another title, made outside YAAP.
    edit = repository.write_tree(first.commit, [Put("cards/1.md", card(1, "Hand edited"))])
    edited = git(folder, "commit-tree", edit, "-p", first.commit, "-m", "hand edit")
    git(folder, "update-ref", REF, edited)
    indexer.sync(PROJECT, repository)
    assert cards(sessions)[1].title == "Hand edited"


@pytest.mark.parametrize(
    ("entries", "message"),
    [
        ([("100644", "cards/1.md", b"no front matter")], "front matter"),
        ([("100644", "cards/1.md", card(2).encode())], "id"),
        ([("100644", "cards/1.md", card(1, kind="epic").encode())], "kind"),
        ([("100644", "cards/1.md", b"x" * (MAX_FILE_BYTES + 1))], "too large"),
        ([("100644", "cards/1.md", b"\xff\xfe\x00")], "UTF-8"),
    ],
)
def test_a_file_that_cannot_be_read_is_indexed_as_a_broken_card_with_its_error(
    indexer, repository, sessions, folder, entries, message
) -> None:
    start_board(sessions)
    plumb_commit(folder, entries)
    indexer.sync(PROJECT, repository)
    row = cards(sessions)[1]
    assert row.broken and row.board_column is None and row.title == "" and message in (row.error or "")


def test_a_broken_version_is_indexed_without_state_or_budget(indexer, repository, sessions) -> None:
    start_board(sessions)
    other = VERSION.replace("0.8.0", "0.9.0")
    repository.commit_changes([Put("versions/0.8.0.md", "garbage"), Put("versions/0.9.0.md", other)], "m", None)
    indexer.sync(PROJECT, repository)
    with sessions() as session:
        rows = {row.name: row for row in session.scalars(select(BoardVersionRow))}
    assert (rows["0.8.0"].state, rows["0.8.0"].budget_usd) == (None, None)
    assert rows["0.9.0"].state == "open"


def test_when_the_indexed_commit_is_gone_files_are_compared_by_blob(indexer, repository, sessions, folder, monkeypatch):
    start_board(sessions)
    repository.commit_changes([Put("cards/1.md", card(1)), Put("cards/2.md", card(2))], "m", None)
    indexer.sync(PROJECT, repository)
    # The branch is rewritten: card 1 is as before, card 2 changed, card 3 is new, and the old commit is unreachable.
    plumb_commit(
        folder,
        [("100644", "cards/1.md", card(1).encode()), ("100644", "cards/2.md", card(2, "New title").encode()),
         ("100644", "cards/3.md", card(3).encode())],
    )
    git(folder, "reflog", "expire", "--expire=now", "--all")
    git(folder, "gc", "-q", "--prune=now")
    seen: list[list[str]] = []
    real = repository.read_many

    def recording(paths, commit=None):
        seen.append(sorted(paths))
        return real(paths, commit)

    monkeypatch.setattr(repository, "read_many", recording)
    result = indexer.sync(PROJECT, repository)
    assert result.rebuilt and result.changed == 2
    assert seen == [["cards/2.md", "cards/3.md"]]
    assert cards(sessions)[2].title == "New title"
    assert sorted(cards(sessions)) == [1, 2, 3]


def test_a_rebuild_drops_rows_without_a_file_and_rereads_everything(indexer, repository, sessions) -> None:
    start_board(sessions)
    repository.commit_changes([Put("cards/1.md", card(1)), Put("versions/0.8.0.md", VERSION)], "m", None)
    indexer.sync(PROJECT, repository)
    with sessions() as session:
        session.add(BoardCardRow(project_id=PROJECT, card_id=9, path="cards/9.md", blob_sha="0" * 40))
        row = session.scalars(select(BoardCardRow).where(BoardCardRow.card_id == 1)).one()
        row.title = "stale"
        session.commit()
    result = indexer.rebuild(PROJECT, repository)
    assert result.rebuilt and result.changed == 2
    found = cards(sessions)
    assert sorted(found) == [1] and found[1].title == "A card"


def test_card_ids_are_never_reused_after_a_card_is_deleted(indexer, repository, sessions) -> None:
    start_board(sessions)
    first = repository.commit_changes([Put("cards/1.md", card(1)), Put("cards/2.md", card(2))], "m", None)
    indexer.sync(PROJECT, repository)
    assert state(sessions).next_card_id == 3
    repository.commit_changes([Delete("cards/2.md")], "m", first.commit)
    indexer.sync(PROJECT, repository)
    assert sorted(cards(sessions)) == [1] and state(sessions).next_card_id == 3


def test_the_next_card_id_never_goes_below_what_was_handed_out(indexer, repository, sessions) -> None:
    start_board(sessions, next_card_id=40)
    repository.commit_changes([Put("cards/1.md", card(1))], "m", None)
    indexer.sync(PROJECT, repository)
    assert state(sessions).next_card_id == 40


def test_a_hand_made_card_with_a_high_id_lifts_the_next_card_id(indexer, repository, sessions) -> None:
    start_board(sessions)
    repository.commit_changes([Put("cards/57.md", card(57))], "m", None)
    indexer.sync(PROJECT, repository)
    assert state(sessions).next_card_id == 58


def test_syncs_of_other_projects_do_not_touch_a_projects_rows(indexer, repository, sessions) -> None:
    start_board(sessions)
    with sessions() as session:
        session.add(BoardState(project_id=2, storage="branch"))
        session.add(BoardCardRow(project_id=2, card_id=1, path="cards/1.md", blob_sha="0" * 40, title="other"))
        session.commit()
    repository.commit_changes([Put("cards/1.md", card(1))], "m", None)
    indexer.rebuild(PROJECT, repository)
    with sessions() as session:
        other = session.scalars(select(BoardCardRow).where(BoardCardRow.project_id == 2)).one()
    assert other.title == "other"
