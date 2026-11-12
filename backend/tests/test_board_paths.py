"""The board's path allow list: only card and version files, a branch ref and full commit hashes reach git."""

import pytest

from app.board.errors import InvalidCard, PathRefused
from app.board.paths import (
    BoardFile,
    card_path,
    check_commit,
    check_path,
    check_ref,
    classify,
    version_path,
)
from app.board.repository import Delete, Put, check_changes, parse_tree_listing

BLOB = "a" * 40


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("cards/1.md", BoardFile("card", "cards/1.md", card_id=1)),
        ("cards/123456789.md", BoardFile("card", "cards/123456789.md", card_id=123456789)),
        ("versions/0.8.0.md", BoardFile("version", "versions/0.8.0.md", version="0.8.0")),
        ("versions/next_big-one.md", BoardFile("version", "versions/next_big-one.md", version="next_big-one")),
        ("cards/0.md", None),
        ("cards/01.md", None),
        ("cards/1234567890.md", None),
        ("cards/-1.md", None),
        ("cards/1.MD", None),
        ("cards/1.md/x", None),
        ("cards/1.txt", None),
        ("cards/a.md", None),
        ("cards/1.md\n", None),
        ("/cards/1.md", None),
        ("./cards/1.md", None),
        ("../cards/1.md", None),
        ("cards/../cards/1.md", None),
        ("cards\\1.md", None),
        ("cards//1.md", None),
        ("CARDS/1.md", None),
        ("other/cards/1.md", None),
        ("versions/.md", None),
        ("versions/..md", None),
        ("versions/.hidden.md", None),
        ("versions/-x.md", None),
        ("versions/a b.md", None),
        ("versions/a/b.md", None),
        (f"versions/{'a' * 41}.md", None),
        ("README.md", None),
        (".gitattributes", None),
        ("", None),
    ],
)
def test_only_card_and_version_files_are_board_files(path: str, expected: BoardFile | None) -> None:
    assert classify(path) == expected
    if expected is None:
        with pytest.raises(PathRefused):
            check_path(path)
    else:
        assert check_path(path) == expected


@pytest.mark.parametrize(
    ("card_id", "expected"),
    [(1, "cards/1.md"), (42, "cards/42.md"), (999_999_999, "cards/999999999.md"), (0, None), (-3, None), (10**9, None)],
)
def test_card_paths_come_from_positive_ids_only(card_id: int, expected: str | None) -> None:
    if expected is None:
        with pytest.raises(PathRefused):
            card_path(card_id)
    else:
        assert card_path(card_id) == expected
        assert classify(expected) == BoardFile("card", expected, card_id=card_id)


def test_a_boolean_is_not_a_card_id() -> None:
    with pytest.raises(PathRefused):
        card_path(True)


@pytest.mark.parametrize(
    ("name", "allowed"),
    [
        ("0.8.0", True),
        ("v1", True),
        ("a" * 40, True),
        ("a" * 41, False),
        ("", False),
        (".", False),
        ("..", False),
        ("../x", False),
        ("a/b", False),
        ("-rc", False),
        ("x y", False),
        ("x\n", False),
    ],
)
def test_version_paths_come_from_plain_names_only(name: str, allowed: bool) -> None:
    if allowed:
        assert version_path(name) == f"versions/{name}.md"
    else:
        with pytest.raises(PathRefused):
            version_path(name)


@pytest.mark.parametrize(
    ("ref", "allowed"),
    [
        ("refs/heads/yaap-board", True),
        ("refs/heads/boards/main", True),
        ("refs/heads/v0.8", True),
        ("yaap-board", False),
        ("refs/tags/yaap-board", False),
        ("refs/remotes/origin/yaap-board", False),
        ("refs/heads/", False),
        ("refs/heads/a..b", False),
        ("refs/heads/a.lock", False),
        ("refs/heads/a.", False),
        ("refs/heads/.hidden", False),
        ("refs/heads/a//b", False),
        ("refs/heads/a/", False),
        ("refs/heads/a b", False),
        ("refs/heads/a~1", False),
        ("refs/heads/a^", False),
        ("refs/heads/a:b", False),
        ("refs/heads/@{-1}", False),
        ("refs/heads/-x", False),
        ("--output=x", False),
        (f"refs/heads/{'a' * 200}", False),
    ],
)
def test_the_board_ref_is_a_plain_branch(ref: str, allowed: bool) -> None:
    if allowed:
        assert check_ref(ref) == ref
    else:
        with pytest.raises(PathRefused):
            check_ref(ref)


@pytest.mark.parametrize(
    ("commit", "allowed"),
    [
        ("0123456789abcdef0123456789abcdef01234567", True),
        ("a" * 64, True),
        ("a" * 39, False),
        ("a" * 41, False),
        ("A" * 40, False),
        ("HEAD", False),
        ("refs/heads/yaap-board", False),
        (f"{'a' * 40}^", False),
        (f"{'a' * 40}^{{tree}}", False),
        ("--all", False),
        ("", False),
    ],
)
def test_commits_are_full_hashes_only(commit: str, allowed: bool) -> None:
    if allowed:
        assert check_commit(commit) == commit
    else:
        with pytest.raises(PathRefused):
            check_commit(commit)


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ([Put("cards/1.md", "x")], None),
        ([Put("cards/1.md", "x"), Delete("cards/2.md"), Put("versions/v1.md", "y")], None),
        ([Put("README.md", "x")], PathRefused),
        ([Delete(".gitattributes")], PathRefused),
        ([Put("cards/../x.md", "x")], PathRefused),
        ([Put("cards/1.md", "x"), Delete("cards/1.md")], PathRefused),
        ([Put("cards/1.md", "x" * 200_001)], InvalidCard),
        ([Put("cards/1.md", "é" * 100_001)], InvalidCard),
        ([], InvalidCard),
    ],
)
def test_a_commit_writes_only_allowed_paths_once_within_the_cap(
    changes: list[Put | Delete], error: type[Exception] | None
) -> None:
    if error is None:
        check_changes(changes)
    else:
        with pytest.raises(error):
            check_changes(changes)


@pytest.mark.parametrize(
    ("record", "kept"),
    [
        (f"100644 blob {BLOB}     120\tcards/1.md", True),
        (f"100644 blob {BLOB}     120\tversions/v1.md", True),
        (f"120000 blob {BLOB}      10\tcards/1.md", False),
        (f"100755 blob {BLOB}     120\tcards/1.md", False),
        (f"160000 commit {BLOB}       -\tcards/1.md", False),
        (f"100644 blob {BLOB}     120\tREADME.md", False),
        (f"100644 blob {BLOB}     120\tcards/x.md", False),
        (f"100644 blob {BLOB}       -\tcards/1.md", False),
    ],
)
def test_only_regular_board_files_are_listed(record: str, kept: bool) -> None:
    files = parse_tree_listing(f"{record}\0")
    assert bool(files) is kept
    if kept:
        assert files[0].blob == BLOB and files[0].size == 120
