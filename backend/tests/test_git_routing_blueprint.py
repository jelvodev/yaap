"""Git on a project's Blueprint layer and on a review of its changes runs in the place it is given (the runner), and
never as a process of YAAP."""

import asyncio
import io
import tarfile
from collections.abc import Callable
from pathlib import Path

import anyio
import pytest

from app.blueprint import at_ref, mutations
from app.blueprint.layers import Layer
from app.git import service
from app.git.place import GitPlace
from app.reviews import scope
from app.storage.models import Project
from app.targets.base import CapturedResult


def tar_with(name: str, content: bytes) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as bundle:
        info = tarfile.TarInfo(name)
        info.size = len(content)
        bundle.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


class RecordingExecutor:
    """Answers the git commands a caller sends and records them."""

    def __init__(self) -> None:
        self.commands: list[str] = []

    async def capture(self, command: str, timeout: float) -> CapturedResult:
        self.commands.append(command)
        if "rev-parse" in command:
            return CapturedResult(0, b"abc1234\n", b"")
        if "ls-tree" in command:
            return CapturedResult(0, b".blueprint\n", b"")
        if "archive" in command:
            return CapturedResult(0, tar_with(".blueprint/instructions.md", b"Rule.\n"), b"")
        if "--porcelain=v2" in command:
            return CapturedResult(0, b"# branch.oid abc1234\n# branch.head main\n", b"")
        if "--porcelain" in command:
            return CapturedResult(0, b" M .blueprint/instructions.md\n", b"")
        return CapturedResult(0, b"", b"")


@pytest.fixture
def no_local_git(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_arguments: object, **_keywords: object) -> None:
        raise AssertionError("YAAP started a local git process")

    monkeypatch.setattr(service.subprocess, "run", refuse)
    monkeypatch.setattr(service, "check_repository", refuse)


def in_worker(function: Callable[[], object]) -> object:
    async def call() -> object:
        return await anyio.to_thread.run_sync(function)

    return asyncio.run(call())


@pytest.mark.parametrize("operation", ["commit", "layer_at_ref", "review_changes"])
def test_blueprint_and_review_git_run_on_the_given_executor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_local_git: None, operation: str
) -> None:
    executor = RecordingExecutor()
    place = GitPlace(tmp_path, executor)
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(mutations, "layer_place", lambda _layer: place)
    monkeypatch.setattr(at_ref, "layer_place", lambda _layer: place)
    monkeypatch.setattr(scope, "git_place", lambda _environment_id, _folder: place)
    layer = Layer("project", "Site", tmp_path / ".blueprint", tmp_path, ".blueprint/", 1)

    def commit() -> object:
        return mutations.commit_paths(layer, ["instructions.md"], "Save")

    def layer_at_ref() -> object:
        return at_ref.layer_at_ref(layer, "main", tmp_path / "at-ref")

    def review_changes() -> object:
        project = Project(name="Site", environment_id=1)
        monkeypatch.setattr(scope, "folder_of", lambda _workspace, _project: tmp_path)
        with pytest.raises(scope.NothingToReview):  # the fake repository has no changes, so it stops after the git
            scope.changes_scope(None, tmp_path, project)  # type: ignore[arg-type]  # the session is not reached
        return None

    run = {"commit": commit, "layer_at_ref": layer_at_ref, "review_changes": review_changes}[operation]
    in_worker(run)

    assert executor.commands, "no git command reached the executor"
    assert all("git" in command for command in executor.commands)
    if operation == "commit":
        assert any("commit" in command for command in executor.commands)
    if operation == "layer_at_ref":
        assert (tmp_path / "at-ref" / ".blueprint" / "instructions.md").read_text() == "Rule.\n"
        assert any("archive" in command for command in executor.commands)
