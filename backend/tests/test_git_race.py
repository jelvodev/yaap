"""A filter planted in a repository between YAAP's safety check and git's own read never runs with YAAP's rights.

The race: a process in the runner can write a project folder. If YAAP ran git there, `check_repository()` could pass
and the runner could then insert a clean filter into `.git/config` before git read it, so the filter command would run
in YAAP. These tests plant such a filter right after the check and look for a canary file that the filter creates in
YAAP's side of the machine. The control proves the harness sees the race; the routed tests prove the work-tree and
object commands no longer reach the check or a local git process.
"""

import asyncio
import contextlib
import io
import os
import subprocess
import tarfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import anyio
import pytest

from app.blueprint import at_ref, mutations
from app.blueprint.layers import Layer
from app.evals import case_writer, workspace
from app.evals.baseline import build_project
from app.evals.cases import EvalCase
from app.evals.conversation_facts import commit_before
from app.evals.models import CommandCheck, StartState
from app.git import branches, discard, remote, service
from app.git.common import GitError
from app.git.place import GitPlace, git_place
from app.learning.branches import write_branch
from app.runner.service import RUNNERS
from app.sandbox.client import HelperClient
from app.storage.models import Environment
from app.targets.base import CapturedResult

# Read before the test suite's own fixture switches the runner off for each test.
RUNNER_SETTING_AT_IMPORT = os.environ.get("YAAP_RUNNER", "on").strip().lower()
RUNNER_ON_AT_IMPORT = RUNNER_SETTING_AT_IMPORT not in ("off", "0", "false", "no")
RACE_WORKSPACE = os.environ.get("YAAP_RACE_WORKSPACE", "").strip()
HELPER_URL = os.environ.get("YAAP_SANDBOX_URL", "").strip()


# --- The raced repository ------------------------------------------------------------------------------------------


def git(folder: Path, *arguments: str) -> str:
    """Real git for building the fixture, with a fixed identity and no global configuration."""
    environment = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@localhost",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@localhost",
    }
    completed = subprocess.run(
        ["git", *arguments], cwd=folder, env=environment, capture_output=True, text=True, check=True, timeout=60
    )
    return completed.stdout


def make_repository(folder: Path) -> None:
    """One commit with a tracked file, then a change in the work tree so that status and commit have to read it."""
    folder.mkdir(parents=True)
    git(folder, "init", "--quiet", "--initial-branch=main")
    (folder / "tracked.txt").write_text("first\n", encoding="utf-8")
    git(folder, "add", "tracked.txt")
    git(folder, "commit", "--quiet", "-m", "First")
    (folder / "tracked.txt").write_text("second\n", encoding="utf-8")


def plant_filter(folder: Path, canary: Path) -> None:
    """What a runner process does between the check and git's read: a clean and a smudge filter that create the
    canary, applied to every file. Written with `git config --local`, so it lands in the repository's own config."""
    command = f"sh -c 'echo x > \"{canary.as_posix()}\"; cat'"
    git(folder, "config", "--local", "filter.canary.clean", command)
    git(folder, "config", "--local", "filter.canary.smudge", command)
    (folder / ".gitattributes").write_text("* filter=canary\n", encoding="utf-8")


@dataclass
class Race:
    """The raced repository, the canary the planted filter creates and the calls the wrapper saw."""

    folder: Path
    canary: Path
    checks: list[Path] = field(default_factory=list)


@pytest.fixture
def race(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Race:
    """A real repository whose check in YAAP is followed at once by a planted clean filter."""
    folder = tmp_path / "project"
    canary = tmp_path / "canary.txt"
    make_repository(folder)
    found = Race(folder, canary)
    real_check = service.check_repository

    def check_then_plant(checked: Path) -> None:
        found.checks.append(checked)
        real_check(checked)
        plant_filter(checked, canary)

    monkeypatch.setattr(service, "check_repository", check_then_plant)
    return found


# --- Fakes ---------------------------------------------------------------------------------------------------------


def tar_of(members: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as bundle:
        for name, content in members.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(content)
            bundle.addfile(entry, io.BytesIO(content))
    return stream.getvalue()


ARCHIVE = tar_of({"a.txt": b"hello", ".blueprint/instructions.md": b"Rule.\n"})
STATUS = b"# branch.oid abc\n# branch.head main\n# branch.upstream origin/main\n# branch.ab +0 -0\n"
ANSWERS: list[tuple[str, bytes]] = [
    (" archive ", ARCHIVE),
    ("remote get-url", b"https://github.com/example/site.git\n"),
    ("status --porcelain", STATUS),
    ("symbolic-ref", b"main\n"),
    ("--show-current", b"main\n"),
    ("for-each-ref", b"main\n"),
    ("ls-tree", b".blueprint\n"),
    ("cat-file", b"commit\n"),
    ("rev-list", b"abc123\n"),
    ("rev-parse --verify --quiet refs/heads", b""),
    ("rev-parse", b"abc123\n"),
    ("hash-object", b"blobsha\n"),
    ("write-tree", b"treesha\n"),
    ("commit-tree", b"commitsha\n"),
]


class RunnerStandIn:
    """Stands in for the runner: answers by what a command asks for and records it. It never runs git in YAAP."""

    def __init__(self) -> None:
        self.commands: list[str] = []

    async def capture(self, command: str, timeout: float) -> CapturedResult:
        self.commands.append(command)
        for needle, answer in ANSWERS:
            if needle in command:
                return CapturedResult(0, answer, b"")
        return CapturedResult(0, b"", b"")


@dataclass
class LocalGit:
    """Stands in for YAAP's own git process; any command it is asked to start is a work-tree command that stayed."""

    started: list[list[str]] = field(default_factory=list)

    def __call__(self, command: list[str], **_keywords: object) -> subprocess.CompletedProcess[str]:
        self.started.append(command)
        raise AssertionError(f"YAAP started a local git command: {command}")


def in_worker(function: Callable[[], object]) -> object:
    async def call() -> object:
        return await anyio.to_thread.run_sync(function)

    return asyncio.run(call())


# --- Control: the harness sees the race ----------------------------------------------------------------------------


needs_git = pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True, check=False).returncode != 0,
    reason="git is not installed, so the filter race cannot be reproduced.",
)


@needs_git
def test_git_in_yaaps_own_process_runs_a_filter_planted_after_the_check(race: Race) -> None:
    # A GitError is expected: after the run, the second check finds the planted filter and refuses. By then it ran.
    # (Status is no control: its first command, reading the remote, fails that second check before status reads.)
    with contextlib.suppress(GitError):
        service.commit_all(race.folder, "Second")

    if not race.canary.exists():
        pytest.skip("this git cannot run the planted filter here (no sh to run it), so the race cannot be shown.")
    assert race.checks, "the repository check was not reached"


# --- Routed: the moved paths never reach the check or a local git process ------------------------------------------


@dataclass
class Setup:
    """What an operation needs: where it runs, the stand-in for the runner and the temporary folder."""

    place: GitPlace
    folder: Path
    tmp_path: Path
    monkeypatch: pytest.MonkeyPatch

    def layer(self) -> Layer:
        return Layer("project", "Site", self.folder / ".blueprint", self.folder, ".blueprint/", 1)


def run_status(setup: Setup) -> object:
    return service.repository_status(setup.place)


def run_diff(setup: Setup) -> object:
    return service.repository_diff(setup.place)


def run_commit_all(setup: Setup) -> object:
    return service.commit_all(setup.place, "message", None)


def run_switch_branch(setup: Setup) -> object:
    return branches.switch_branch(setup.place, "feature")


def run_pull_merge(setup: Setup) -> object:
    # The fetch half is a network command and stays in YAAP; the merge half touches the work tree.
    return service.run_git(["merge", "--ff-only", "@{upstream}"], setup.place, timeout=300)


def run_pull_through_remote(setup: Setup) -> object:
    return remote.pull(setup.place, None)


def run_discard(setup: Setup) -> object:
    return discard.discard_changes(setup.place, None)


def run_commit_paths(setup: Setup) -> object:
    setup.monkeypatch.setattr(mutations, "layer_place", lambda _layer: setup.place)
    return mutations.commit_paths(setup.layer(), ["instructions.md"], "Save")


def run_at_ref(setup: Setup) -> object:
    setup.monkeypatch.setattr(at_ref, "layer_place", lambda _layer: setup.place)
    return at_ref.layer_at_ref(setup.layer(), "main", setup.tmp_path / "at-ref")


def run_clone_at(setup: Setup) -> object:
    copy = setup.tmp_path / "copy"
    places = {setup.folder: setup.place, copy: GitPlace(copy, setup.place.executor)}
    setup.monkeypatch.setattr(workspace, "git_place", lambda _environment, folder: places[folder])
    return workspace.clone_at(setup.folder, copy, "deadbeef", 1)


def run_write_branch(setup: Setup) -> object:
    return write_branch(setup.layer(), setup.place, "rules/a.md", "one", "yaap-learning/7", "Add")


def run_baseline_build(setup: Setup) -> object:
    folder = setup.tmp_path / "baseline"
    return build_project(GitPlace(folder, setup.place.executor))


def run_case_writer_commit(setup: Setup) -> object:
    setup.monkeypatch.setattr(case_writer, "commit", lambda *_arguments: None)
    case = EvalCase(
        name="check", prompt="Do it.", start=StartState(commit="abcdef1"), checks=(CommandCheck("passes", "true", 30),)
    )
    return case_writer.save_case(setup.layer(), case, repository=setup.place)


def run_conversation_facts_commit(setup: Setup) -> object:
    return commit_before(setup.place, datetime(2026, 1, 1, tzinfo=UTC))


OPERATIONS: dict[str, Callable[[Setup], object]] = {
    "status": run_status,
    "diff": run_diff,
    "commit_all": run_commit_all,
    "switch_branch": run_switch_branch,
    "pull merge half": run_pull_merge,
    "discard_changes": run_discard,
    "blueprint commit_paths": run_commit_paths,
    "at_ref archive and resolve": run_at_ref,
    "clone_at": run_clone_at,
    "write_branch": run_write_branch,
    "baseline build": run_baseline_build,
    "case writer commit": run_case_writer_commit,
    "conversation facts commit lookup": run_conversation_facts_commit,
}


@pytest.mark.parametrize("name", list(OPERATIONS))
def test_work_tree_git_goes_to_the_runner_and_the_planted_filter_never_runs_in_yaap(
    race: Race, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str
) -> None:
    local = LocalGit()
    monkeypatch.setattr(service.subprocess, "run", local)
    executor = RunnerStandIn()
    setup = Setup(GitPlace(race.folder, executor), race.folder, tmp_path, monkeypatch)

    with contextlib.suppress(GitError):  # the stand-in answers little; what counts is where the commands went
        in_worker(lambda: OPERATIONS[name](setup))

    assert executor.commands, "no git command reached the runner"
    assert race.checks == [], "the repository check ran in YAAP for a work-tree command"
    assert local.started == []
    assert not race.canary.exists()


def test_pull_runs_only_its_network_half_in_yaap_and_the_merge_in_the_runner(
    race: Race, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[list[str]] = []

    def network_only(command: list[str], **_keywords: object) -> subprocess.CompletedProcess[str]:
        started.append(command)
        if "fetch" not in command:
            raise AssertionError(f"YAAP started a local work-tree git command: {command}")
        return subprocess.CompletedProcess(command, 0, "", "")

    # Planting after the fetch's own check would make the second check refuse; this test is about the merge.
    monkeypatch.setattr(service, "check_repository", lambda _folder: None)
    monkeypatch.setattr(service.subprocess, "run", network_only)
    executor = RunnerStandIn()

    in_worker(lambda: remote.pull(GitPlace(race.folder, executor), None))

    assert any("merge --ff-only" in command for command in executor.commands), executor.commands
    assert len(started) == 1 and "fetch" in started[0]
    assert not race.canary.exists()


# --- Real runner (Docker) ------------------------------------------------------------------------------------------


@pytest.mark.skipif(
    not (RUNNER_ON_AT_IMPORT and HELPER_URL and RACE_WORKSPACE),
    reason="needs the Docker setup: YAAP_RUNNER on, YAAP_SANDBOX_URL set and YAAP_RACE_WORKSPACE (the workspace folder "
    "the sandbox helper mounts into runners) set; the suite itself runs with the runner off.",
)
@pytest.mark.parametrize("operation", ["status", "commit_all", "diff"])
def test_a_filter_planted_after_the_check_runs_in_the_runner_and_never_in_yaap(
    client: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """Runs on the real Docker setup: the folder is in the workspace, so `git_place` returns the environment's real
    runner (a container started by the sandbox helper), and the planted filter may only affect that container.

    Run it in the backend container:
    `docker compose exec -e YAAP_RUNNER=on -e YAAP_RACE_WORKSPACE=/data/workspace backend python -m pytest
    tests/test_git_race.py -k runner -q -rs`. The canary lies in YAAP's own temporary folder, which the runner cannot
    see: it exists only if a filter ran in YAAP."""
    app_state = client.app.state  # type: ignore[attr-defined]  # the application fixture is untyped
    with app_state.session_factory() as session:
        slug = session.get_one(Environment, 1).slug
    monkeypatch.setattr(RUNNERS, "enabled", True)
    monkeypatch.setattr(RUNNERS, "workspace_dir", Path(RACE_WORKSPACE))
    monkeypatch.setattr(RUNNERS.manager, "helper", HelperClient.from_environment())
    folder = Path(RACE_WORKSPACE) / slug / f"race-{operation}"
    canary = tmp_path / "canary.txt"
    make_repository(folder)
    checks: list[Path] = []

    def check_then_plant(checked: Path) -> None:
        checks.append(checked)
        plant_filter(checked, canary)

    monkeypatch.setattr(service, "check_repository", check_then_plant)
    try:
        place = git_place(1, folder)
        assert place.executor is not None, "the folder was not routed to a runner"
        with contextlib.suppress(GitError):
            if operation == "status":
                in_worker(lambda: service.repository_status(place))
            elif operation == "diff":
                in_worker(lambda: service.repository_diff(place))
            else:
                in_worker(lambda: service.commit_all(place, "Second"))
    finally:
        for path in sorted(folder.rglob("*"), reverse=True):
            path.chmod(0o700)
            path.rmdir() if path.is_dir() else path.unlink()
        folder.rmdir()

    assert checks == []
    assert not canary.exists()
