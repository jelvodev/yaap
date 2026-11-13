"""The decisions that guard a merge into a version: branch and folder names, who may be merged, which checks run and
what bundle may move the version branch."""

import pytest

from app.board.version_branch import (
    VersionBranchError,
    may_move_version,
    merge_folder,
    merge_refusal,
    version_branch_name,
)
from app.board.version_merge import CardCommands, merge_commands

COMMIT = "a" * 40
OTHER = "b" * 40


@pytest.mark.parametrize(
    ("version", "expected"),
    [("1", "yaap/v1"), ("0.8.0", "yaap/v0.8.0"), ("rc_1-b", "yaap/vrc_1-b"), ("A", "yaap/vA")],
)
def test_version_branch_name_accepts_board_version_names(version: str, expected: str) -> None:
    assert version_branch_name(version) == expected
    assert merge_folder(version) == f".yaap/builds/_v{version}"


@pytest.mark.parametrize(
    "version",
    ["", "..", "1..2", "1.lock", "-1", "a/b", "a b", "x" * 41, ".1", "1\n2", "a~b", "a:b", "1."],
)
def test_version_branch_name_refuses_names_that_are_not_safe(version: str) -> None:
    with pytest.raises(VersionBranchError):
        version_branch_name(version)
    with pytest.raises(VersionBranchError):
        merge_folder(version)


@pytest.mark.parametrize(
    ("column", "review_done", "version", "plan_status", "branch", "default", "refused"),
    [
        ("review", True, "1", "done", "yaap/card-4", "main", False),
        ("done", False, "1", "done", "yaap/card-4", "main", False),
        ("review", False, "1", "done", "yaap/card-4", "main", True),
        ("build", True, "1", "done", "yaap/card-4", "main", True),
        ("design", True, "1", "done", "yaap/card-4", "main", True),
        ("review", True, None, "done", "yaap/card-4", "main", True),
        ("review", True, "1", "building", "yaap/card-4", "main", True),
        ("review", True, "1", None, "yaap/card-4", "main", True),
        ("review", True, "1", "done", None, "main", True),
        ("review", True, "1", "done", "", "main", True),
        ("review", True, "1", "done", "-x", "main", True),
        ("review", True, "1", "done", "main", "main", True),
        ("review", True, "1", "done", "yaap/v1", "main", True),
        ("review", True, "..", "done", "yaap/card-4", "main", True),
    ],
)
def test_merge_refusal_only_lets_a_reviewed_built_card_with_its_own_branch_through(
    column: str,
    review_done: bool,
    version: str | None,
    plan_status: str | None,
    branch: str | None,
    default: str,
    refused: bool,
) -> None:
    reason = merge_refusal(column, review_done, version, plan_status, branch, default)
    assert (reason is not None) is refused


@pytest.mark.parametrize(
    ("version_commands", "shown", "cards", "run", "refused_commands"),
    [
        (("pytest",), ("pytest",), [], ("pytest",), ()),
        (("pytest",), (), [], (), ("pytest",)),
        (("pytest ",), ("pytest",), [], (), ("pytest ",)),
        (("pytest",), ("pytest && evil",), [], (), ("pytest",)),
        ((), (), [CardCommands(("make test",), ("make test",))], ("make test",), ()),
        ((), (), [CardCommands(("make test",), ())], (), ("make test",)),
        ((), (), [CardCommands(("make test && evil",), ("make test",))], (), ("make test && evil",)),
        ((), ("make test",), [CardCommands(("make test",), ())], (), ("make test",)),
        (
            ("pytest",),
            ("pytest",),
            [CardCommands(("pytest", "lint"), ("pytest", "lint")), CardCommands(("lint",), ("lint",))],
            ("pytest", "lint"),
            (),
        ),
        (("pytest",), ("pytest",), [CardCommands(("pytest",), ())], ("pytest",), ()),
    ],
)
def test_merge_commands_run_only_shown_version_commands_and_approved_card_commands(
    version_commands: tuple[str, ...],
    shown: tuple[str, ...],
    cards: list[CardCommands],
    run: tuple[str, ...],
    refused_commands: tuple[str, ...],
) -> None:
    allowed, refused = merge_commands(version_commands, shown, cards)
    assert allowed == run
    assert tuple(command for command, _why in refused) == refused_commands


@pytest.mark.parametrize(
    ("refs", "ok"),
    [
        ([(COMMIT, "refs/heads/yaap/v1")], True),
        ([(OTHER, "refs/heads/yaap/v1")], False),
        ([(COMMIT, "refs/heads/yaap/v2")], False),
        ([(COMMIT, "refs/heads/main")], False),
        ([(COMMIT, "refs/heads/yaap/v1"), (COMMIT, "refs/heads/main")], False),
        ([(COMMIT, "refs/heads/yaap/v1"), (OTHER, "refs/heads/yaap/v1")], False),
        ([(COMMIT, "HEAD")], False),
        ([], False),
    ],
)
def test_version_branch_moves_only_for_a_bundle_with_exactly_that_ref_at_that_commit(
    refs: list[tuple[str, str]], ok: bool
) -> None:
    assert may_move_version(refs, "yaap/v1", COMMIT) is ok
