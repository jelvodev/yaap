"""Merging a card into its version's branch on real temporary repositories: a clean merge that passes its checks moves
only the version branch; a conflict or a failing check moves nothing; the default branch, the checked-out branch and the
card's branch are never written."""

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from app.board.version_branch import (
    VersionBranchError,
    default_branch,
    deliver_version,
    make_merge_copy,
    merge_card,
    merge_folder,
    merged_into,
    remove_merge_copy,
    version_tip,
)
from app.board.version_merge import MergeJob, MergeResult, VersionMerges
from app.plans.workspace import copy_folder
from tests.board_support import host_git

VERSION = "1"
VERSION_BRANCH = "yaap/v1"


def same_place(folder: Path) -> Path:
    return folder


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """main with two files, checked out; nothing else."""
    folder = tmp_path / "site"
    folder.mkdir()
    host_git("init", "-b", "main", cwd=folder)
    (folder / "app.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (folder / "lib.py").write_text("lib\n", encoding="utf-8")
    host_git("add", ".", cwd=folder)
    host_git("commit", "-m", "Start", cwd=folder)
    return folder


def card_branch(project: Path, name: str, files: dict[str, str]) -> str:
    """A branch off main with one commit that writes the files; main is checked out again afterwards."""
    host_git("checkout", "-q", "-b", name, "main", cwd=project)
    for path, content in files.items():
        (project / path).write_text(content, encoding="utf-8")
    host_git("add", ".", cwd=project)
    host_git("commit", "-m", f"Card {name}", cwd=project)
    host_git("checkout", "-q", "main", cwd=project)
    return name


def refs(project: Path) -> dict[str, str]:
    lines = host_git("for-each-ref", "--format=%(refname) %(objectname)", cwd=project).splitlines()
    return dict(line.split(" ") for line in lines)


def snapshot(project: Path) -> tuple[dict[str, str], str, str]:
    """Every ref, the checked-out branch and the working tree's status."""
    head = host_git("symbolic-ref", "HEAD", cwd=project)
    status = host_git("status", "--porcelain", "--untracked-files=all", cwd=project)
    return refs(project), head, status


def merges() -> VersionMerges:
    """The merge runner without a board: only its git work and its order of decisions are used."""
    return VersionMerges(None, None, None, None)  # type: ignore[arg-type]  # the git work needs none of them


def job(project: Path, branch: str, run: tuple[str, ...] = (), refused: tuple[tuple[str, str], ...] = ()) -> MergeJob:
    return MergeJob(
        1,
        7,
        "Health",
        VERSION,
        branch,
        None,  # type: ignore[arg-type]  # the plan is read only by the checks, which these tests replace
        project,
        same_place,
        project,
        run,
        refused,
    )


def attempt(runner: VersionMerges, work: MergeJob) -> MergeResult:
    return asyncio.run(runner.attempt(work))


def checks_that(output: str | None) -> Callable[[MergeJob], Awaitable[str | None]]:
    """A stand-in for the run of the checks: passes (None) or fails with the output."""

    async def check(_work: MergeJob) -> str | None:
        return output

    return check


# --- The git work --------------------------------------------------------------------------------------------------


def test_the_default_branch_is_main_and_never_a_yaap_branch(project: Path) -> None:
    name, commit = default_branch(project)
    assert (name, commit) == ("main", host_git("rev-parse", "main", cwd=project).strip())
    host_git("checkout", "-q", "-b", "yaap/side", cwd=project)
    host_git("branch", "-D", "main", cwd=project)
    with pytest.raises(VersionBranchError):
        default_branch(project)


def test_a_clean_merge_moves_only_the_version_branch(project: Path) -> None:
    branch = card_branch(project, "yaap/card-1", {"lib.py": "lib changed\n"})
    before = snapshot(project)
    assert version_tip(project, VERSION) is None

    copy = make_merge_copy(project, same_place, VERSION, branch)
    outcome = merge_card(copy, "Merge card #1")
    assert outcome.merged and outcome.commit is not None
    # The merge happened in the copy only.
    assert snapshot(project) == before
    deliver_version(project, copy, outcome.commit)
    remove_merge_copy(project, VERSION)

    after = refs(project)
    assert after["refs/heads/yaap/v1"] == outcome.commit
    assert {name: sha for name, sha in after.items() if name != "refs/heads/yaap/v1"} == before[0]
    assert snapshot(project)[1:] == before[1:]
    assert host_git("show", f"{outcome.commit}:lib.py", cwd=project) == "lib changed\n"
    assert merged_into(project, VERSION, branch)
    assert not copy_folder(project, f"_v{VERSION}").exists()


def test_the_second_card_is_merged_on_top_of_the_first(project: Path) -> None:
    first = card_branch(project, "yaap/card-1", {"lib.py": "lib changed\n"})
    second = card_branch(project, "yaap/card-2", {"app.py": "one\ntwo\nthree\nfour\n"})
    for number, branch in enumerate((first, second), start=1):
        copy = make_merge_copy(project, same_place, VERSION, branch)
        outcome = merge_card(copy, f"Merge card #{number}")
        assert outcome.merged and outcome.commit is not None
        deliver_version(project, copy, outcome.commit)
        remove_merge_copy(project, VERSION)
    assert merged_into(project, VERSION, first) and merged_into(project, VERSION, second)
    tip = version_tip(project, VERSION)
    assert host_git("show", f"{tip}:lib.py", cwd=project) == "lib changed\n"
    assert host_git("show", f"{tip}:app.py", cwd=project).endswith("four\n")


def test_a_conflict_is_reported_with_its_files_and_leaves_the_copy_clean(project: Path) -> None:
    first = card_branch(project, "yaap/card-1", {"app.py": "one\nchanged by one\nthree\n"})
    second = card_branch(project, "yaap/card-2", {"app.py": "one\nchanged by two\nthree\n"})
    copy = make_merge_copy(project, same_place, VERSION, first)
    deliver_version(project, copy, merge_card(copy, "Merge card #1").commit or "")
    remove_merge_copy(project, VERSION)
    tip = version_tip(project, VERSION)
    before = snapshot(project)

    copy = make_merge_copy(project, same_place, VERSION, second)
    outcome = merge_card(copy, "Merge card #2")

    assert not outcome.merged and outcome.commit is None
    assert "app.py" in outcome.output
    assert host_git("status", "--porcelain", cwd=copy.folder) == ""
    remove_merge_copy(project, VERSION)
    assert snapshot(project) == before
    assert version_tip(project, VERSION) == tip


def test_the_version_branch_is_not_moved_when_it_moved_meanwhile(project: Path) -> None:
    first = card_branch(project, "yaap/card-1", {"lib.py": "lib changed\n"})
    second = card_branch(project, "yaap/card-2", {"app.py": "one\ntwo\nthree\nfour\n"})
    copy = make_merge_copy(project, same_place, VERSION, first)
    commit = merge_card(copy, "Merge card #1").commit or ""
    # Another merge delivers first (its copy replaces the folder, as a second merge would).
    other = make_merge_copy(project, same_place, VERSION, second)
    deliver_version(project, other, merge_card(other, "Merge card #2").commit or "")
    moved = version_tip(project, VERSION)

    with pytest.raises(VersionBranchError):
        deliver_version(project, copy, commit)
    assert version_tip(project, VERSION) == moved
    remove_merge_copy(project, VERSION)


def test_the_version_branch_cannot_be_moved_while_it_is_checked_out(project: Path) -> None:
    branch = card_branch(project, "yaap/card-1", {"lib.py": "lib changed\n"})
    host_git("branch", VERSION_BRANCH, "main", cwd=project)
    host_git("checkout", "-q", VERSION_BRANCH, cwd=project)
    copy = make_merge_copy(project, same_place, VERSION, branch)
    outcome = merge_card(copy, "Merge card #1")
    with pytest.raises(VersionBranchError, match="checked out"):
        deliver_version(project, copy, outcome.commit or "")
    remove_merge_copy(project, VERSION)


@pytest.mark.parametrize("branch", ["main", VERSION_BRANCH, "does-not-exist", "-x"])
def test_a_card_branch_that_is_the_default_the_version_or_missing_cannot_be_merged(project: Path, branch: str) -> None:
    host_git("branch", VERSION_BRANCH, "main", cwd=project)
    before = snapshot(project)
    with pytest.raises(VersionBranchError):
        make_merge_copy(project, same_place, VERSION, branch)
    assert snapshot(project) == before
    assert not (project / merge_folder(VERSION)).exists()


# --- The merge as a whole (the run of the checks stubbed) ------------------------------------------------------------


def test_a_merge_that_passes_its_checks_is_delivered(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    branch = card_branch(project, "yaap/card-1", {"lib.py": "lib changed\n"})
    runner = merges()
    monkeypatch.setattr(runner, "check", checks_that(None))

    result = attempt(runner, job(project, branch))

    assert result.status == "merged" and result.commit == version_tip(project, VERSION)
    remove_merge_copy(project, VERSION)


def test_a_merge_with_a_failing_check_moves_nothing_in_the_project(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    branch = card_branch(project, "yaap/card-1", {"lib.py": "lib changed\n"})
    before = snapshot(project)
    runner = merges()
    monkeypatch.setattr(runner, "check", checks_that("pytest\n1 failed"))

    result = attempt(runner, job(project, branch, run=("pytest",)))

    assert result.status == "failed" and "1 failed" in result.output and result.commit is None
    assert snapshot(project) == before
    assert version_tip(project, VERSION) is None
    remove_merge_copy(project, VERSION)


def test_a_conflicting_merge_is_a_conflict_result_and_moves_nothing(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = card_branch(project, "yaap/card-1", {"app.py": "one\nchanged by one\nthree\n"})
    second = card_branch(project, "yaap/card-2", {"app.py": "one\nchanged by two\nthree\n"})
    runner = merges()
    monkeypatch.setattr(runner, "check", checks_that(None))
    assert attempt(runner, job(project, first)).status == "merged"
    remove_merge_copy(project, VERSION)
    before = snapshot(project)

    result = attempt(runner, job(project, second))

    assert result.status == "conflict" and "app.py" in result.output
    assert snapshot(project) == before
    remove_merge_copy(project, VERSION)


def test_a_command_that_was_not_approved_stops_the_merge_before_any_git_work(project: Path) -> None:
    branch = card_branch(project, "yaap/card-1", {"lib.py": "lib changed\n"})
    before = snapshot(project)

    result = attempt(merges(), job(project, branch, refused=(("evil", "Not shown."),)))

    assert result.status == "failed" and "Not run: evil" in result.output
    assert snapshot(project) == before
    assert not (project / merge_folder(VERSION)).exists()
