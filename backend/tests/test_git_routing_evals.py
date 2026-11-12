"""Git for evals and learning runs where the folder's place says: in the executor of a place with one, never as a
local process on a folder the runner can write."""

import asyncio
import io
import tarfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import anyio
import pytest

from app.blueprint.layers import Layer
from app.evals import case_writer, workspace
from app.evals.baseline import build_project
from app.evals.cases import EvalCase
from app.evals.conversation_facts import commit_before
from app.evals.models import CommandCheck, StartState
from app.git import service
from app.git.place import GitPlace
from app.learning.branches import branch_exists, remove_branch, write_branch
from app.targets.base import CapturedResult

ANSWERS = {
    "cat-file": b"commit\n",
    "rev-list": b"abc123\n",
    "rev-parse --verify --quiet refs/heads": b"",
    "rev-parse": b"abc123\n",
    "hash-object": b"blobsha\n",
    "write-tree": b"treesha\n",
    "commit-tree": b"commitsha\n",
}


def tar_of(name: str, content: bytes) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as bundle:
        entry = tarfile.TarInfo(name)
        entry.size = len(content)
        bundle.addfile(entry, io.BytesIO(content))
    return stream.getvalue()


class RecordingExecutor:
    """Records every command; answers the ones in ANSWERS and succeeds quietly on the rest."""

    def __init__(self, archive: bytes = b"", root: Path | None = None) -> None:
        self.commands: list[str] = []
        self.archive = archive
        self.root = root
        self.seen_files: dict[str, bytes] = {}

    async def capture(self, command: str, timeout: float) -> CapturedResult:
        self.commands.append(command)
        if self.root is not None:
            for scratch in (self.root / ".git").glob("yaap-learning-*"):
                self.seen_files[scratch.suffix] = scratch.read_bytes()
        if " archive " in command:
            return CapturedResult(0, self.archive, b"")
        for needle, answer in ANSWERS.items():
            if needle in command:
                return CapturedResult(0, answer, b"")
        return CapturedResult(0, b"", b"")

    def ran(self, word: str) -> bool:
        return any(f" {word} " in f" {command} " or f"{word} " in command for command in self.commands)


def fail_when_called(*_arguments: object, **_keywords: object) -> None:
    raise AssertionError("YAAP started a local git process")


def in_worker[Result](function: Callable[[], Result]) -> Result:
    async def call() -> Result:
        return await anyio.to_thread.run_sync(function)

    return asyncio.run(call())


@pytest.fixture(autouse=True)
def no_local_git(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service.subprocess, "run", fail_when_called)


def test_the_start_commit_is_read_in_the_projects_place_and_the_copy_is_made_in_the_workspace_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, copy = tmp_path / "project", tmp_path / "evals" / "copy"
    (project / ".git").mkdir(parents=True)
    source = RecordingExecutor(archive=tar_of("a.txt", b"hello"))
    target = RecordingExecutor()
    places = {project: GitPlace(project, source), copy: GitPlace(copy, target)}
    monkeypatch.setattr(workspace, "git_place", lambda _environment, folder: places[folder])

    in_worker(lambda: workspace.clone_at(project, copy, "deadbeef", 1))

    assert source.ran("cat-file") and source.ran("archive") and not source.ran("clone")
    assert [word for word in ("init", "add", "commit") if target.ran(word)] == ["init", "add", "commit"]
    assert (copy / "a.txt").read_bytes() == b"hello"


def test_the_baseline_project_is_built_through_the_executor_and_leaves_no_patch_file(tmp_path: Path) -> None:
    folder = tmp_path / "stockroom"
    executor = RecordingExecutor()

    commits = in_worker(lambda: build_project(GitPlace(folder, executor)))

    assert executor.ran("init") and executor.ran("apply") and executor.ran("commit")
    assert commits.start == "abc123"
    assert not (folder / ".yaap-baseline.patch").exists()
    assert any(folder.iterdir())


def test_a_saved_case_reads_its_commit_through_the_executor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layer = Layer("project", "p", tmp_path / ".blueprint", tmp_path)
    (tmp_path / ".git").mkdir()
    executor = RecordingExecutor()
    monkeypatch.setattr(case_writer, "commit", lambda *_arguments: None)
    case = EvalCase(
        name="check", prompt="Do it.", start=StartState(commit="abcdef1"), checks=(CommandCheck("passes", "true", 30),)
    )

    saved = in_worker(lambda: case_writer.save_case(layer, case, repository=GitPlace(tmp_path, executor)))

    assert saved.commit == "abc123"
    assert executor.ran("rev-parse")


def test_the_commit_before_a_moment_is_listed_through_the_executor(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    executor = RecordingExecutor()

    found = in_worker(lambda: commit_before(GitPlace(tmp_path, executor), datetime(2026, 1, 1, tzinfo=UTC)))

    assert found == "abc123" and executor.ran("rev-list")


def test_a_learning_branch_is_written_with_plumbing_in_the_executor_and_its_files_are_removed(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    layer = Layer("project", "p", tmp_path / ".blueprint", tmp_path, ".blueprint/")
    executor = RecordingExecutor(root=tmp_path)
    place = GitPlace(tmp_path, executor)

    commit = in_worker(lambda: write_branch(layer, place, "rules/a.md", "one\r\ntwo", "yaap-learning/7", "Add"))

    assert commit == "commitsha"
    for word in ("read-tree", "hash-object", "update-index", "write-tree", "commit-tree", "update-ref"):
        assert executor.ran(word), word
    # The runner saw the content as a file in the repository's own folder, with LF endings, by relative paths.
    assert executor.seen_files[".blob"] == b"one\ntwo"
    assert all("GIT_INDEX_FILE=.git/yaap-learning-7.index" in c for c in executor.commands if "read-tree" in c)
    assert list((tmp_path / ".git").glob("yaap-learning-*")) == []


@pytest.mark.parametrize("operation", ["exists", "remove"])
def test_learning_branches_are_looked_up_and_removed_through_the_executor(tmp_path: Path, operation: str) -> None:
    (tmp_path / ".git").mkdir()
    layer = Layer("project", "p", tmp_path / ".blueprint", tmp_path)
    executor = RecordingExecutor()
    place = GitPlace(tmp_path, executor)
    action = (
        (lambda: branch_exists(layer, place, "yaap-learning/7"))
        if operation == "exists"
        else (lambda: remove_branch(layer, place, "yaap-learning/7"))
    )

    in_worker(action)

    assert executor.ran("rev-parse")
