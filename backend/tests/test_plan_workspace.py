"""A build works in a copy of the project with its own repository, and only its branch comes back.

On real temporary repositories: the copy passes the repository check, the project's branch, index and status stay as
they were, delivery brings exactly the build branch at the recorded commit and refuses anything else, and a hostile
configuration written into the copy never reaches the project."""

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.git.bundles import BRANCH_PREFIX, bundle_create
from app.git.safety import UnsafeRepository, check_repository
from app.git.service import commit_all, run_git
from app.plans.model import PlanStatus
from app.plans.workspace import (
    BuildCopyError,
    build_folders,
    bundle_header,
    check_build_bundle,
    copy_folder,
    deliver_build,
    make_build_copy,
    read_base,
    remove_build_copy,
    should_prune,
)

BRANCH = "yaap/plan-1-add-a-health-endpoint"


def host_git(*arguments: str, cwd: Path) -> str:
    """Plain git as a user (or a command in the runner) would run it."""
    completed = subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.com", *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout


def same_place(folder: Path) -> Path:
    return folder


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project with two commits on main, a second branch, an uncommitted change and an untracked file."""
    folder = tmp_path / "site"
    folder.mkdir()
    host_git("init", "-b", "main", cwd=folder)
    (folder / "app.py").write_text("print('one')\n", encoding="utf-8")
    host_git("add", ".", cwd=folder)
    host_git("commit", "-m", "One", cwd=folder)
    host_git("branch", "other", cwd=folder)
    (folder / "app.py").write_text("print('two')\n", encoding="utf-8")
    host_git("commit", "-am", "Two", cwd=folder)
    (folder / "app.py").write_text("print('mine, not committed')\n", encoding="utf-8")
    (folder / "notes.txt").write_text("untracked\n", encoding="utf-8")
    return folder


def snapshot(folder: Path) -> tuple[str, str, str, str]:
    """The project's checked-out branch, its index, its status and its branches."""
    return (
        host_git("symbolic-ref", "HEAD", cwd=folder),
        host_git("ls-files", "--stage", cwd=folder),
        host_git("status", "--porcelain", "--untracked-files=all", cwd=folder),
        host_git("for-each-ref", "--format=%(refname) %(objectname)", cwd=folder),
    )


def build_step(copy: Path, name: str) -> str:
    (copy / name).write_text("built\n", encoding="utf-8")
    commit_all(copy, f"Step: {name}")
    return run_git(["rev-parse", "HEAD"], copy).stdout.strip()


def test_the_copy_is_an_ordinary_repository_and_the_project_is_untouched(project: Path) -> None:
    before = snapshot(project)
    base = read_base(project)
    assert base.branch == "main" and base.uncommitted == 2
    copy = make_build_copy(project, same_place, 1, base.commit, BRANCH)
    assert copy == copy_folder(project, 1)
    check_repository(copy)
    assert host_git("symbolic-ref", "--short", "HEAD", cwd=copy).strip() == BRANCH
    assert host_git("rev-parse", "HEAD", cwd=copy).strip() == base.commit
    assert (copy / "app.py").read_text(encoding="utf-8") == "print('two')\n"
    # Only the base branch's history: the project's other branch is not in the copy.
    assert host_git("for-each-ref", "--format=%(refname)", cwd=copy).split() == [BRANCH_PREFIX + BRANCH]
    assert not (copy / "notes.txt").exists()
    assert snapshot(project) == before
    assert build_folders(project) == [1]


def test_a_hostile_config_in_the_copy_makes_git_refuse_it(project: Path, tmp_path: Path) -> None:
    base = read_base(project)
    copy = make_build_copy(project, same_place, 2, base.commit, BRANCH)
    tip = build_step(copy, "c.py")
    marker = tmp_path / "pwned"
    host_git("config", "core.fsmonitor", f'echo pwned >> "{marker.as_posix()}"', cwd=copy)
    with pytest.raises(UnsafeRepository, match="core.fsmonitor"):
        check_repository(copy)
    with pytest.raises(BuildCopyError, match="core.fsmonitor"):
        deliver_build(project, copy, BRANCH, base.commit, tip)
    assert not marker.exists()


def test_delivery_brings_exactly_the_build_branch(project: Path) -> None:
    base = read_base(project)
    copy = make_build_copy(project, same_place, 3, base.commit, BRANCH)
    host_git("branch", "side", cwd=copy)
    tip = build_step(copy, "health.py")
    before = snapshot(project)
    assert deliver_build(project, copy, BRANCH, base.commit, tip) is True
    after = snapshot(project)
    assert after[:3] == before[:3]
    refs = set(after[3].splitlines()) - set(before[3].splitlines())
    assert refs == {f"{BRANCH_PREFIX}{BRANCH} {tip}"}


def test_nothing_is_delivered_without_a_commit(project: Path) -> None:
    base = read_base(project)
    copy = make_build_copy(project, same_place, 4, base.commit, BRANCH)
    assert deliver_build(project, copy, BRANCH, base.commit, base.commit) is False
    assert BRANCH not in host_git("branch", "--list", cwd=project)


def test_delivery_refuses_a_commit_the_build_did_not_record(project: Path) -> None:
    base = read_base(project)
    copy = make_build_copy(project, same_place, 5, base.commit, BRANCH)
    recorded = build_step(copy, "one.py")
    build_step(copy, "planted.py")  # a commit made in the copy after the build recorded its last step
    with pytest.raises(BuildCopyError, match="does not hold exactly"):
        deliver_build(project, copy, BRANCH, base.commit, recorded)
    assert BRANCH not in host_git("branch", "--list", cwd=project)


def test_delivery_refuses_a_commit_that_does_not_descend_from_the_base(project: Path) -> None:
    base = read_base(project)
    copy = make_build_copy(project, same_place, 6, base.commit, BRANCH)
    host_git("checkout", "--orphan", "fresh", cwd=copy)
    (copy / "other.py").write_text("x\n", encoding="utf-8")
    host_git("add", ".", cwd=copy)
    host_git("commit", "-m", "Unrelated", cwd=copy)
    unrelated = host_git("rev-parse", "HEAD", cwd=copy).strip()
    host_git("branch", "-f", BRANCH, unrelated, cwd=copy)
    with pytest.raises(BuildCopyError, match="does not descend"):
        deliver_build(project, copy, BRANCH, base.commit, unrelated)
    assert BRANCH not in host_git("branch", "--list", cwd=project)


def test_delivery_leaves_an_existing_branch_alone(project: Path) -> None:
    base = read_base(project)
    copy = make_build_copy(project, same_place, 7, base.commit, BRANCH)
    tip = build_step(copy, "a.py")
    host_git("branch", BRANCH, "other", cwd=project)
    existing = host_git("rev-parse", BRANCH, cwd=project)
    with pytest.raises(BuildCopyError, match="already exists"):
        deliver_build(project, copy, BRANCH, base.commit, tip)
    assert host_git("rev-parse", BRANCH, cwd=project) == existing


def test_a_hostile_config_written_in_the_copy_never_reaches_the_project(project: Path) -> None:
    base = read_base(project)
    copy = make_build_copy(project, same_place, 8, base.commit, BRANCH)
    tip = build_step(copy, "b.py")
    host_git("config", "alias.st", "!echo pwned", cwd=copy)
    host_git("config", "remote.evil.url", "https://example.com/evil.git", cwd=copy)
    config_before = (project / ".git" / "config").read_bytes()
    assert deliver_build(project, copy, BRANCH, base.commit, tip) is True
    assert (project / ".git" / "config").read_bytes() == config_before
    hooks = project / ".git" / "hooks"
    assert not any(path.name.endswith("pwned") for path in hooks.iterdir())


@pytest.mark.parametrize(
    ("refs", "allowed"),
    [
        ([("a" * 40, f"refs/heads/{BRANCH}")], True),
        ([("b" * 40, f"refs/heads/{BRANCH}")], False),
        ([("a" * 40, f"refs/heads/{BRANCH}"), ("c" * 40, "refs/heads/side")], False),
        ([("a" * 40, "refs/heads/main")], False),
        ([("a" * 40, "refs/tags/v1")], False),
        ([], False),
    ],
)
def test_a_build_bundle_holds_exactly_the_build_branch(refs: list[tuple[str, str]], allowed: bool) -> None:
    if allowed:
        check_build_bundle(refs, BRANCH, "a" * 40)
    else:
        with pytest.raises(BuildCopyError):
            check_build_bundle(refs, BRANCH, "a" * 40)


def test_the_bundle_header_is_read_by_yaap(project: Path) -> None:
    base = read_base(project)
    data = bundle_create(project, ["HEAD", "refs/heads/other"])
    prerequisites, refs = bundle_header(data)
    assert prerequisites == ()
    assert (base.commit, "HEAD") in refs and any(ref == "refs/heads/other" for _, ref in refs)
    with pytest.raises(BuildCopyError):
        bundle_header(b"not a bundle")


def test_a_failed_copy_leaves_nothing_behind(project: Path) -> None:
    with pytest.raises(BuildCopyError, match="moved"):
        make_build_copy(project, same_place, 9, "0" * 40, BRANCH)
    assert build_folders(project) == []
    remove_build_copy(project, 9)  # removing what is not there is fine


NOW = datetime(2026, 11, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    ("status", "age_days", "merged", "pruned"),
    [
        ("building", 30, False, False),
        ("queued", 30, False, False),
        ("interrupted", 30, False, False),
        ("approved", 30, False, False),
        ("failed", 6, False, False),
        ("failed", 8, False, True),
        ("cancelled", 8, False, True),
        ("done", 13, False, False),
        ("done", 13, True, True),
        ("done", 15, False, True),
        (None, 0, False, True),
    ],
)
def test_prune_keeps_a_building_plans_copy(
    status: PlanStatus | None, age_days: int, merged: bool, pruned: bool
) -> None:
    finished = NOW - timedelta(days=age_days)
    assert should_prune(status, finished, merged, NOW) is pruned
