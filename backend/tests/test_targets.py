import asyncio
import os
from types import SimpleNamespace

import pytest

from app.agent.runs import RunConfig, RunRegistry
from app.agent.gate import call_tool
from app.agent.tools import tools_for
from app.agent.policy import ToolPolicy
from app.targets.base import CommandResult
from app.targets.container import LocalExecutor, LocalFiles
from app.projects.service import ProjectError, project_path


def test_project_path_rejects_parent_traversal(tmp_path):
    with pytest.raises(ProjectError):
        project_path(tmp_path, SimpleNamespace(folder="env/../../outside"))


def test_project_path_rejects_a_symlinked_project_root(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "env").symlink_to(outside, target_is_directory=True)

    try:
        project_path(workspace, SimpleNamespace(folder="env/project"))
    except ProjectError as error:
        assert "symbolic link" in str(error)
    else:
        raise AssertionError("a symlinked project parent was accepted")


def test_search_skips_symlinks_outside_project_and_non_regular_files(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside-canary\n")
    (root / "outside.txt").symlink_to(outside)
    if hasattr(os, "mkfifo"):
        os.mkfifo(root / "pipe.txt")

    results = asyncio.run(LocalFiles(str(root)).search("canary", ".", "*.txt", 10))
    assert results == []


def test_search_keeps_internal_file_symlinks(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "source.txt").write_text("inside-marker\n")
    (root / "link.txt").symlink_to(root / "source.txt")

    results = asyncio.run(LocalFiles(str(root)).search("inside-marker", ".", "link.txt", 10))
    assert results == ["link.txt:1: inside-marker"]


def test_run_refuses_project_root_replaced_between_tool_calls(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    registry, run = start_run(project)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "canary.txt").write_text("outside-canary")
    project.rename(tmp_path / "original")
    project.symlink_to(outside, target_is_directory=True)

    (output, is_error), = calls(registry, run, [("read_file", {"path": "canary.txt"})])
    assert is_error
    assert "replaced" in output


def start_run(folder, target=None):
    registry = RunRegistry()
    run = registry.start(RunConfig(
        conversation_id=1,
        environment_id=1,
        project_id=1,
        project_folder=folder,
        blueprint_layers=[],
        tool_names=tools_for(has_project=True, has_attachments=False),
        policy=ToolPolicy(commands=("echo",)),
        target=target,
    ))
    return registry, run


def calls(registry, run, steps):
    async def scenario():
        return [await call_tool(registry, run, name, arguments) for name, arguments in steps]

    return asyncio.run(scenario())


def test_editing_a_crlf_file_still_matches_lines_with_lf(tmp_path):
    (tmp_path / "windows.txt").write_bytes(b"first\r\nsecond\r\n")
    registry, run = start_run(tmp_path)
    (output, is_error), = calls(
        registry, run, [("edit_file", {"path": "windows.txt", "old_string": "first\nsecond", "new_string": "one\ntwo"})]
    )
    assert not is_error, output
    assert (tmp_path / "windows.txt").read_bytes() == b"one\ntwo\n"


class RecordingTarget:
    """A target that works on the local disk but records every use, as an SSH target would be used."""

    kind = "recording"

    def __init__(self) -> None:
        self.used: list[str] = []

    def files(self, root: str):
        target = self

        class Files(LocalFiles):
            async def read_bytes(self, path):
                target.used.append(f"read {path}")
                return await super().read_bytes(path)

            async def write_bytes(self, path, data):
                target.used.append(f"write {path}")
                await super().write_bytes(path, data)

        return Files(root)

    def executor(self, root: str):
        target = self

        class Executor(LocalExecutor):
            async def run(self, command, env, timeout, script=None, **_options):
                target.used.append(f"run {command}")
                return CommandResult(0, "from the target")

        return Executor(root)


def test_tools_work_through_the_runs_target(tmp_path):
    target = RecordingTarget()
    registry, run = start_run(tmp_path, target)
    results = calls(
        registry,
        run,
        [
            ("write_file", {"path": "a.txt", "content": "hello\n"}),
            ("read_file", {"path": "a.txt"}),
            ("edit_file", {"path": "a.txt", "old_string": "hello", "new_string": "bye"}),
            ("run_command", {"command": "echo hi"}),
        ],
    )
    assert not any(is_error for _, is_error in results), results
    assert results[3][0] == "Exit code 0\nfrom the target"
    assert target.used == ["write a.txt", "read a.txt", "read a.txt", "write a.txt", "run echo hi"]
    assert (tmp_path / "a.txt").read_text() == "bye\n"
