"""The board repository on real temporary git repositories: the first commit has no parent, the work tree, index and
checked-out branch are never touched, a moved head is refused, and only regular allow-listed files are read."""

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.board.errors import BoardCheckedOut, BoardUnavailable, HeadMoved, InvalidCard, PathRefused
from app.board.paths import MAX_FILE_BYTES
from app.board.repository import BoardRepository, Delete, Put
from app.board.squash import BOARD_AUTHOR

REF = "refs/heads/yaap-board"
CARD = "---\nid: 1\n---\nbody\n"


def git(folder: Path, *arguments: str, env: dict[str, str] | None = None, check: bool = True) -> str:
    import os

    result = subprocess.run(
        ["git", "-c", "user.name=Dev", "-c", "user.email=dev@example.com", "-c", "commit.gpgsign=false", *arguments],
        cwd=folder,
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=check,
    )
    return result.stdout.strip()


@dataclass(frozen=True)
class Snapshot:
    """Everything a board write must leave as it is."""

    status: str
    index: str
    head: str
    branch: str
    files: dict[str, str]


def snapshot(folder: Path) -> Snapshot:
    files = {p.name: p.read_text(encoding="utf-8") for p in folder.iterdir() if p.is_file()}
    return Snapshot(
        git(folder, "status", "--porcelain"),
        git(folder, "ls-files", "-s"),
        git(folder, "rev-parse", "HEAD"),
        git(folder, "symbolic-ref", "HEAD"),
        files,
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project with one commit on main, a staged change, an unstaged change and an untracked file."""
    folder = tmp_path / "project"
    folder.mkdir()
    git(folder, "init", "-q", "-b", "main")
    (folder / "app.txt").write_text("one\n", encoding="utf-8")
    (folder / "other.txt").write_text("other\n", encoding="utf-8")
    git(folder, "add", ".")
    git(folder, "commit", "-q", "-m", "first")
    (folder / "app.txt").write_text("two\n", encoding="utf-8")
    (folder / "other.txt").write_text("changed\n", encoding="utf-8")
    git(folder, "add", "other.txt")
    (folder / "untracked.txt").write_text("new\n", encoding="utf-8")
    return folder


@pytest.fixture
def board(project: Path) -> BoardRepository:
    return BoardRepository(project)


def put_card(board: BoardRepository, number: int, expected: str | None, text: str = CARD, squash: bool = False):
    return board.commit_changes([Put(f"cards/{number}.md", text)], "Board: 1 card added", expected, squash)


def test_the_first_board_commit_is_an_orphan_with_the_fixed_author(board, project) -> None:
    assert board.head() is None and board.list_files() == ()
    written = put_card(board, 1, None)
    info = board.commit_info(written.commit)
    assert board.head() == written.commit and not written.squashed
    assert info.parents == ()
    assert info.author == BOARD_AUTHOR and info.author_email == "board@yaap.invalid"
    assert git(project, "log", "-1", "--format=%cn <%ce>", REF) == BOARD_AUTHOR
    assert info.message == "Board: 1 card added"
    assert git(project, "merge-base", "--is-ancestor", "main", REF, check=False) == ""


def test_writing_the_board_leaves_the_work_tree_index_and_branch_alone(board, project) -> None:
    before = snapshot(project)
    first = put_card(board, 1, None)
    second = put_card(board, 2, first.commit)
    board.commit_changes([Delete("cards/1.md")], "Board: 1 card dropped", second.commit)
    assert snapshot(project) == before
    assert not list((project / ".git").glob("yaap-board-*"))


def test_puts_replace_and_deletes_remove_files_and_a_later_commit_has_the_earlier_one_as_parent(board) -> None:
    first = board.commit_changes([Put("cards/1.md", "a\n"), Put("versions/0.8.0.md", "v\n")], "m", None)
    second = board.commit_changes([Put("cards/1.md", "b\n"), Put("cards/2.md", "c\n")], "m2", first.commit)
    assert board.commit_info(second.commit).parents == (first.commit,)
    assert {f.path for f in board.list_files()} == {"cards/1.md", "cards/2.md", "versions/0.8.0.md"}
    assert board.read("cards/1.md").text == "b\n"
    assert board.read("cards/1.md", first.commit).text == "a\n"
    third = board.commit_changes([Delete("cards/1.md"), Delete("versions/0.8.0.md")], "m3", second.commit)
    assert [f.path for f in board.list_files(third.commit)] == ["cards/2.md"]
    assert board.read("cards/1.md") is None
    assert board.changed_paths(first.commit, third.commit) == ("cards/1.md", "cards/2.md", "versions/0.8.0.md")
    assert board.changed_paths(None, third.commit) == ("cards/2.md",)


def test_the_repository_is_valid_after_many_writes(board, project) -> None:
    head = None
    for number in range(1, 6):
        head = put_card(board, number, head).commit
    board.commit_changes([Delete("cards/3.md")], "m", head)
    git(project, "fsck", "--strict", "--no-dangling")


def test_a_head_that_moved_is_refused_with_the_current_head(board) -> None:
    first = put_card(board, 1, None)
    second = put_card(board, 2, first.commit)
    with pytest.raises(HeadMoved) as stale:
        put_card(board, 3, first.commit)
    assert stale.value.current == second.commit
    with pytest.raises(HeadMoved) as exists:
        put_card(board, 3, None)
    assert exists.value.current == second.commit
    assert board.head() == second.commit


def test_an_update_that_loses_the_race_for_the_ref_is_refused(board, project, monkeypatch) -> None:
    first = put_card(board, 1, None)
    real_write_tree = board.write_tree

    def write_then_move(base, changes):
        tree = real_write_tree(base, changes)
        git(project, "update-ref", REF, git(project, "commit-tree", "-m", "rival", tree), first.commit)
        return tree

    monkeypatch.setattr(board, "write_tree", write_then_move)
    with pytest.raises(HeadMoved):
        put_card(board, 2, first.commit)


def test_writing_the_same_content_makes_no_new_commit(board) -> None:
    first = put_card(board, 1, None)
    again = put_card(board, 1, first.commit)
    assert again.commit == first.commit and not again.squashed


def test_crlf_in_content_is_stored_as_lf_without_filters(board, project) -> None:
    (project / ".gitattributes").write_text("* text eol=crlf\n", encoding="utf-8")
    written = put_card(board, 1, None, "a\r\nb\r\n")
    assert board.read("cards/1.md").text == "a\nb\n"
    assert git(project, "cat-file", "-p", f"{written.commit}:cards/1.md") == "a\nb"


def test_the_board_branch_checked_out_is_refused(board, project) -> None:
    first = put_card(board, 1, None)
    git(project, "stash", "-u", "-q")
    git(project, "checkout", "-q", "yaap-board")
    assert board.is_checked_out()
    with pytest.raises(BoardCheckedOut):
        put_card(board, 2, first.commit)


@pytest.mark.parametrize(
    "ref",
    [
        "yaap-board",
        "refs/tags/board",
        "refs/heads/",
        "refs/heads/../x",
        "refs/heads/-x",
        "refs/heads/a b",
        "refs/heads/a..b",
        "refs/heads/x.lock",
        "refs/heads/a.lock/b",
        "refs/heads/x" + "y" * 200,
    ],
)
def test_a_ref_outside_the_branches_is_refused(project, ref) -> None:
    with pytest.raises(PathRefused):
        BoardRepository(project, ref)


@pytest.mark.parametrize(
    "changes",
    [
        [],
        [Put("notes.md", "x")],
        [Put("cards/1.md", "x"), Delete("cards/1.md")],
        [Put("../cards/1.md", "x")],
        [Put("cards/1.md", "x" * (MAX_FILE_BYTES + 1))],
    ],
)
def test_bad_changes_are_refused_before_anything_is_written(board, changes) -> None:
    with pytest.raises((InvalidCard, PathRefused)):
        board.commit_changes(changes, "m", None)
    assert board.head() is None


def test_a_commit_needs_a_message(board) -> None:
    with pytest.raises(InvalidCard):
        board.commit_changes([Put("cards/1.md", "x")], "  ", None)


def plumb_commit(project: Path, entries: list[tuple[str, str, bytes]]) -> str:
    """A commit on the board ref made from raw (mode, path, content) entries, bypassing BoardRepository."""
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as scratch:
        env = {"GIT_INDEX_FILE": str(Path(scratch) / "index")}
        for mode, path, content in entries:
            blob = subprocess.run(
                ["git", "hash-object", "-w", "--stdin"], cwd=project, input=content, capture_output=True, check=True
            ).stdout.decode().strip()
            git(project, "update-index", "--add", "--cacheinfo", f"{mode},{blob},{path}", env=env)
        tree = git(project, "write-tree", env=env)
    commit = git(project, "commit-tree", tree, "-m", "plumbed", env={"GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", **os.environ})
    git(project, "update-ref", REF, commit)
    return commit


def test_symlinks_executables_and_other_files_are_ignored(board, project) -> None:
    head = plumb_commit(
        project,
        [
            ("100644", "cards/1.md", b"real\n"),
            ("120000", "cards/2.md", b"../../app.txt"),
            ("100755", "cards/3.md", b"#!/bin/sh\n"),
            ("100644", "README.md", b"hi\n"),
            ("100644", "cards/sub/4.md", b"x\n"),
            ("100644", "versions/0.8.0.md", b"v\n"),
        ],
    )
    assert [f.path for f in board.list_files(head)] == ["cards/1.md", "versions/0.8.0.md"]
    found = board.read_many(["cards/1.md", "cards/2.md", "cards/3.md", "versions/0.8.0.md"], head)
    assert sorted(found) == ["cards/1.md", "versions/0.8.0.md"]
    assert board.read("cards/2.md", head) is None
    assert board.changed_paths(None, head) == ("cards/1.md", "versions/0.8.0.md")


def test_writing_over_a_symlink_in_the_tree_makes_a_regular_file(board, project) -> None:
    head = plumb_commit(project, [("120000", "cards/2.md", b"../../app.txt")])
    written = board.commit_changes([Put("cards/2.md", "now a file\n")], "m", head)
    assert git(project, "ls-tree", written.commit, "cards/2.md").startswith("100644 blob")
    assert board.read("cards/2.md").text == "now a file\n"


def test_a_blob_over_the_cap_is_reported_without_reading_it_and_binary_is_not_text(board, project) -> None:
    head = plumb_commit(
        project,
        [
            ("100644", "cards/1.md", b"x" * (MAX_FILE_BYTES + 1)),
            ("100644", "cards/2.md", b"\xff\xfe\x00bad"),
            ("100644", "cards/3.md", "é\n".encode()),
        ],
    )
    found = board.read_many(["cards/1.md", "cards/2.md", "cards/3.md"], head)
    assert (found["cards/1.md"].text, found["cards/1.md"].error) == (None, "too_large")
    assert (found["cards/2.md"].text, found["cards/2.md"].error) == (None, "not_text")
    assert (found["cards/3.md"].text, found["cards/3.md"].error) == ("é\n", None)


def test_many_files_are_read_in_batches(board) -> None:
    changes = [Put(f"cards/{n}.md", f"card {n}\n") for n in range(1, 211)]
    written = board.commit_changes(changes, "many", None)
    found = board.read_many([c.path for c in changes], written.commit)
    assert len(found) == 210 and found["cards/210.md"].text == "card 210\n"
    assert len(board.list_files()) == 210


@pytest.mark.parametrize("commit", ["main", "HEAD", "--all", "abc", "A" * 40, "x" * 40])
def test_a_commit_that_is_not_a_full_hash_never_reaches_git(board, commit) -> None:
    with pytest.raises(PathRefused):
        board.list_files(commit)


def test_a_read_of_a_path_off_the_allow_list_is_refused(board) -> None:
    put_card(board, 1, None)
    with pytest.raises(PathRefused):
        board.read("../app.txt")


def test_a_missing_commit_is_not_found_and_a_missing_branch_has_no_head(board) -> None:
    assert board.head() is None and board.read("cards/1.md") is None and board.read_many(["cards/1.md"]) == {}
    assert not board.has_commit("0" * 40)


def test_a_git_folder_that_is_not_a_plain_folder_is_refused(tmp_path) -> None:
    folder = tmp_path / "worktree"
    folder.mkdir()
    git(folder, "init", "-q")
    git_dir = folder / ".git"
    moved = tmp_path / "elsewhere.git"
    git_dir.rename(moved)
    git_dir.write_text(f"gitdir: {moved.as_posix()}\n", encoding="utf-8")
    with pytest.raises(BoardUnavailable):
        BoardRepository(folder).commit_changes([Put("cards/1.md", "x")], "m", None)


def test_a_head_that_is_the_boards_own_young_and_local_is_replaced_when_asked(board, project) -> None:
    first = put_card(board, 1, None)
    second = put_card(board, 2, first.commit, squash=True)
    assert second.squashed
    assert board.commit_info(second.commit).parents == ()
    assert {f.path for f in board.list_files()} == {"cards/1.md", "cards/2.md"}
    assert git(project, "rev-list", "--count", REF) == "1"


def test_a_head_on_a_remote_tracking_ref_is_never_replaced(board, project) -> None:
    first = put_card(board, 1, None)
    git(project, "update-ref", "refs/remotes/origin/yaap-board", first.commit)
    assert board.on_any_remote(first.commit)
    second = put_card(board, 2, first.commit, squash=True)
    assert not second.squashed
    assert board.commit_info(second.commit).parents == (first.commit,)


def test_a_head_by_someone_else_is_never_replaced(board, project) -> None:
    head = plumb_commit(project, [("100644", "cards/1.md", b"hand made\n")])
    assert board.commit_info(head).author != BOARD_AUTHOR
    written = board.commit_changes([Put("cards/2.md", "x\n")], "m", head, squash=True)
    assert not written.squashed
    assert board.commit_info(written.commit).parents == (head,)


def test_a_squash_of_a_second_commit_keeps_the_parent_of_the_replaced_one(board) -> None:
    first = put_card(board, 1, None)
    second = put_card(board, 2, first.commit)
    third = put_card(board, 3, second.commit, squash=True)
    assert third.squashed and board.commit_info(third.commit).parents == (first.commit,)
