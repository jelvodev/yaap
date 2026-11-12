"""The project panel's git work-tree commands run on the repository's place (the runner), not in YAAP."""

import asyncio
import subprocess
from collections.abc import Callable
from pathlib import Path

import anyio
import pytest

from app.api.git import Repository
from app.git import branches, discard, remote, service
from app.git.place import GitPlace
from app.projects.service import create_cloned_project
from app.storage.models import Environment
from app.targets.base import CapturedResult

NETWORK = ("fetch", "push", "clone")


class RecordingExecutor:
    """Answers each command by what it asks for and records it."""

    def __init__(self) -> None:
        self.commands: list[str] = []

    async def capture(self, command: str, timeout: float) -> CapturedResult:
        self.commands.append(command)
        output = b""
        if "remote get-url" in command:
            output = b"https://github.com/example/site.git\n"
        elif "status --porcelain" in command:
            output = b"# branch.oid abc\n# branch.head main\n# branch.upstream origin/main\n# branch.ab +0 -0\n"
        elif "symbolic-ref" in command:
            output = b"main\n"
        elif "--show-current" in command or "for-each-ref" in command:
            output = b"main\n"
        return CapturedResult(0, output, b"")


class LocalGit:
    """Stands in for YAAP's own git process: only the network commands may start one."""

    def __init__(self) -> None:
        self.started: list[list[str]] = []

    def __call__(self, command: list[str], **_keywords: object) -> subprocess.CompletedProcess[str]:
        self.started.append(command)
        if not any(word in command for word in NETWORK):
            raise AssertionError(f"YAAP started a local git command: {command}")
        return subprocess.CompletedProcess(command, 0, "", "")


def in_worker(function: Callable[[], object]) -> object:
    async def call() -> object:
        return await anyio.to_thread.run_sync(function)

    return asyncio.run(call())


@pytest.fixture
def local_git(monkeypatch: pytest.MonkeyPatch) -> LocalGit:
    local = LocalGit()
    monkeypatch.setattr(service.subprocess, "run", local)
    return local


def make_place(tmp_path: Path) -> tuple[GitPlace, RecordingExecutor]:
    (tmp_path / ".git").mkdir()
    executor = RecordingExecutor()
    return GitPlace(tmp_path, executor), executor


OPERATIONS: list[tuple[str, Callable[[GitPlace], object], str]] = [
    ("status", service.repository_status, "status"),
    ("diff", service.repository_diff, "diff"),
    ("log", lambda place: service.repository_log(place, None, 10), "log"),
    ("commit", lambda place: service.commit_all(place, "message", None), "commit"),
    ("branches list", branches.list_branches, "for-each-ref"),
    ("branch create", lambda place: branches.create_branch(place, "feature"), "feature"),
    ("branch switch", lambda place: branches.switch_branch(place, "feature"), "feature"),
    ("branch delete", lambda place: branches.delete_branch(place, "feature"), "feature"),
    ("pull merge", lambda place: remote.pull(place, None), "merge --ff-only"),
    ("discard", lambda place: discard.discard_changes(place, None), "status"),
]


@pytest.mark.parametrize(("name", "operation", "expected"), OPERATIONS, ids=[item[0] for item in OPERATIONS])
def test_panel_operation_runs_on_the_executor_and_starts_no_local_git(
    tmp_path: Path, local_git: LocalGit, name: str, operation: Callable[[GitPlace], object], expected: str
) -> None:
    place, executor = make_place(tmp_path)
    try:
        in_worker(lambda: operation(place))
    except service.GitError:
        pass  # The fake answers little; what counts is where the commands went.

    assert any(expected in command for command in executor.commands), executor.commands
    assert all(any(word in command for word in NETWORK) for command in local_git.started)


def test_clone_checks_out_on_the_place(tmp_path: Path, local_git: LocalGit) -> None:
    executor = RecordingExecutor()
    target = tmp_path / "site"

    in_worker(lambda: remote.clone_repository("https://github.com/example/site.git", target, None, GitPlace(target, executor)))

    assert any("checkout --force main" in command for command in executor.commands)
    assert len(local_git.started) == 1 and "clone" in local_git.started[0]


def test_new_cloned_project_gets_a_checkout_place_for_its_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.projects import service as projects

    places: list[tuple[int, Path]] = []
    checkouts: list[GitPlace | None] = []

    def place_of(environment_id: int, folder: Path) -> GitPlace:
        places.append((environment_id, folder))
        return GitPlace(folder, RecordingExecutor())

    def record_clone(url: str, target: Path, token: str | None, checkout: GitPlace | None = None) -> None:
        checkouts.append(checkout)

    class Session:
        def scalar(self, *_arguments: object) -> None:
            return None

        def add(self, _item: object) -> None:
            pass

        def flush(self) -> None:
            pass

        def commit(self) -> None:
            pass

    monkeypatch.setattr(projects, "git_place", place_of)
    monkeypatch.setattr(projects, "clone_repository", record_clone)
    environment = Environment(id=7, name="Work", slug="work")

    create_cloned_project(Session(), tmp_path, environment, "Site", "https://github.com/example/site.git", None)  # type: ignore[arg-type]  # minimal session double

    assert places and places[0][0] == 7 and places[0][1].name == "site"
    assert checkouts and checkouts[0] is not None and checkouts[0].executor is not None


def test_repository_place_uses_the_environment_of_the_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api import git as api_git

    calls: list[tuple[int, Path]] = []
    monkeypatch.setattr(api_git, "git_place", lambda environment_id, folder: calls.append((environment_id, folder)))
    Repository(id="project-1", kind="project", label="Site", folder="workspace/site", path=tmp_path, environment_id=3).place()

    assert calls == [(3, tmp_path)]
