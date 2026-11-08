import asyncio

from app.agent.runs import RunConfig, RunRegistry
from app.agent.gate import call_tool
from app.agent.tools import tools_for
from app.agent.policy import ToolPolicy
from app.targets.base import CommandResult
from app.targets.container import LocalExecutor, LocalFiles


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
