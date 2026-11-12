import asyncio
from collections.abc import Callable
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import anyio
import pytest

from app.git import executor as git_executor
from app.git.branches import list_branches
from app.git.executor import command_environment, run_worktree_git
from app.git.place import GitPlace
from app.git.service import GitError, commit_all, init_repository, repository_diff, repository_status
from app.git.safety import UnsafeRepository, check_repository
from app.agent.git_tools import git_status
from app.agent.runs import RunConfig, RunRegistry
from app.agent.tools import tools_for
from app.runner.service import RunnerTarget
from app.sandbox.service import SandboxManager, SandboxSpec
from app.targets.base import CapturedResult, CommandResult
from app.targets.ssh import SshExecutor, SshSettings


def captured(result: CommandResult) -> CapturedResult:
    return CapturedResult(result.exit_code, result.output.encode(), b"", result.timed_out)


class RecordingExecutor:
    def __init__(self, result: CommandResult | None = None) -> None:
        self.command = ""
        self.timeout = 0.0
        self.result = result or CommandResult(0, "ok")

    async def capture(self, command: str, timeout: float) -> CapturedResult:
        self.command = command
        self.timeout = timeout
        return captured(self.result)


def in_worker(function: Callable[..., object], *arguments: object) -> object:
    """Call a sync git service function the way a router does: from a worker thread of the event loop."""

    async def call() -> object:
        return await anyio.to_thread.run_sync(function, *arguments)

    return asyncio.run(call())


class ScriptedExecutor(RecordingExecutor):
    def __init__(self, responses: list[tuple[str, CommandResult]]) -> None:
        super().__init__()
        self.responses = responses
        self.commands: list[str] = []

    async def capture(self, command: str, timeout: float) -> CapturedResult:
        self.command = command
        self.commands.append(command)
        expected, result = self.responses.pop(0)
        assert expected in command
        return captured(result)


class LocalGitExecutor:
    """A target-executor harness that runs the exact argv and fixed environment against a temporary repository."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    async def capture(self, command: str, timeout: float) -> CapturedResult:
        words = shlex.split(command)
        assert words[:2] == ["env", "-i"]
        git_index = words.index("git")
        process_environment = dict(
            assignment.split("=", 1) for assignment in words[2:git_index]
        )
        # The test host needs its own git executable path; git configuration remains the helper's isolated environment.
        process_environment["PATH"] = os.environ.get("PATH", "")
        if "SYSTEMROOT" in os.environ:
            process_environment["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
        completed = subprocess.run(
            [shutil.which("git") or words[git_index], *words[git_index + 1 :]],
            cwd=self.folder,
            env=process_environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        return CapturedResult(completed.returncode, completed.stdout.encode(), completed.stderr.encode())


class FilterRaceExecutor(LocalGitExecutor):
    def __init__(self, folder: Path, marker: Path) -> None:
        super().__init__(folder)
        self.marker = marker
        self.inserted = False

    async def capture(self, command: str, timeout: float) -> CapturedResult:
        if not self.inserted:
            python_code = (
                "import sys; from pathlib import Path; Path(" + repr(str(self.marker)) + ").write_text('ran'); "
                "sys.stdout.buffer.write(sys.stdin.buffer.read())"
            )
            filter_command = f"{shlex.quote(sys.executable)} -c {shlex.quote(python_code)}"
            subprocess.run(
                ["git", "config", "--local", "filter.race.clean", filter_command],
                cwd=self.folder,
                check=True,
                capture_output=True,
            )
            (self.folder / ".gitattributes").write_text("*.txt filter=race\n", encoding="utf-8")
            self.inserted = True
        return await super().capture(command, timeout)


def test_worktree_git_uses_a_clean_fixed_environment_and_quotes_arguments() -> None:
    executor = RecordingExecutor()

    result = asyncio.run(
        run_worktree_git(executor, ["status", "--", "name with spaces; touch nope"], config=("core.quotepath=false",))
    )

    assert result.stdout == "ok"
    words = shlex.split(executor.command)
    assert words[:4] == ["env", "-i", "HOME=/tmp", "PATH=/usr/local/bin:/usr/bin:/bin"]
    assert "GIT_CONFIG_GLOBAL=/dev/null" in words
    assert "GIT_CONFIG_NOSYSTEM=1" in words
    assert any(word.startswith("GIT_CONFIG_KEY_") and word.endswith("=core.hooksPath") for word in words)
    assert words[-3:] == ["status", "--", "name with spaces; touch nope"]


def test_worktree_git_returns_command_errors_without_running_local_git() -> None:
    executor = RecordingExecutor(CommandResult(1, "fatal: unsafe repository"))

    with pytest.raises(GitError, match="git add failed: fatal: unsafe repository"):
        asyncio.run(run_worktree_git(executor, ["add", "--all"]))

    assert shlex.split(executor.command)[-3:] == ["--no-pager", "add", "--all"]


def test_worktree_git_reports_executor_timeout() -> None:
    executor = RecordingExecutor(CommandResult(None, "partial", timed_out=True, stopped="total"))

    with pytest.raises(GitError, match="longer than 30 seconds"):
        asyncio.run(run_worktree_git(executor, ["diff"], timeout=30))


def test_worktree_environment_does_not_include_a_git_home_or_token() -> None:
    environment = command_environment({"GIT_AUTHOR_NAME": "YAAP"})

    assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
    assert environment["GIT_AUTHOR_NAME"] == "YAAP"
    assert not any("TOKEN" in name or name in {"HOME", "PATH"} for name in environment)


def test_runner_status_and_branch_output_are_parsed_like_local_git() -> None:
    executor = ScriptedExecutor(
        [
            ("status --porcelain=v2", CommandResult(0, "# branch.oid (initial)\n# branch.head main\n? new file.txt\n")),
            ("remote get-url origin", CommandResult(0, "https://github.com/acme/site.git\n")),
            (
                "for-each-ref",
                CommandResult(0, "*\0main\0origin/main\0ahead 1\0Add page\n \0feature/ui\0\0\0Work\n"),
            ),
        ]
    )

    place = GitPlace(Path("/project"), executor)
    status = in_worker(repository_status, place)
    branches = in_worker(list_branches, place)

    assert status.branch == "main" and status.has_commits is False
    assert status.remote_url == "https://github.com/acme/site.git"
    assert [(change.path, change.state) for change in status.changes] == [("new file.txt", "untracked")]
    assert [(branch.name, branch.current, branch.track) for branch in branches] == [
        ("main", True, "ahead 1"),
        ("feature/ui", False, ""),
    ]


def test_runner_diff_and_commit_use_only_the_target_executor() -> None:
    dirty_status = (
        "# branch.oid aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n# branch.head main\n"
        "? new file.txt\n"
    )
    tracked_status = (
        "# branch.oid aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n# branch.head main\n"
        "1 M. N... 100644 100644 100644 a b tracked.txt\n"
    )
    executor = ScriptedExecutor(
        [
            ("status --porcelain=v2", CommandResult(0, dirty_status)),
            ("remote get-url origin", CommandResult(2, "")),
            ("diff HEAD", CommandResult(0, "tracked diff\n")),
            ("wc -c < 'new file.txt'", CommandResult(0, "4\n")),
            ("diff --no-index", CommandResult(1, "new file diff\n")),
            ("git --no-pager add --all", CommandResult(0, "")),
            ("status --porcelain=v2", CommandResult(0, tracked_status)),
            ("remote get-url origin", CommandResult(2, "")),
            ("git --no-pager commit -m", CommandResult(0, "")),
            ("rev-parse --short HEAD", CommandResult(0, "deadbee\n")),
        ]
    )

    place = GitPlace(Path("/project"), executor)
    diff = in_worker(repository_diff, place)
    commit = in_worker(commit_all, place, "Save changes", ("Reviewer", "reviewer@example.com"))

    assert diff == "tracked diff\nnew file diff\n"
    assert commit == "deadbee"
    assert not executor.responses


def test_agent_git_status_uses_the_runner_executor_when_target_root_is_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment_dir = tmp_path / "environment"
    project_path = environment_dir / "site"
    project_path.mkdir(parents=True)
    executor = ScriptedExecutor(
        [
            (
                "status --porcelain=v2",
                CommandResult(
                    0,
                    "# branch.oid aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n# branch.head main\n"
                    "# branch.upstream origin/main\n",
                ),
            ),
            ("remote get-url origin", CommandResult(0, "https://github.com/acme/site.git\n")),
            ("for-each-ref", CommandResult(0, "*\0main\0origin/main\0\0Site\n")),
        ]
    )

    async def capture_on_target(target_executor: SshExecutor, command: str, timeout: float) -> CapturedResult:
        assert target_executor.root == "/workspace/site"
        return await executor.capture(command, timeout)

    monkeypatch.setattr(SshExecutor, "capture", capture_on_target)
    runner = RunnerTarget(
        "runner-test",
        SshSettings("runner", 22, "yaap", "private-key", "host-key"),
        SandboxManager(),
        1,
        SandboxSpec("e1", "environment", True, "public-key"),
        environment_dir,
    )

    registry = RunRegistry()
    run = registry.start(
        RunConfig(
            conversation_id=1,
            environment_id=1,
            project_id=1,
            project_folder=project_path,
            blueprint_layers=[],
            tool_names=tools_for(has_project=True, has_attachments=False),
            target=runner,
        )
    )

    output = asyncio.run(git_status(run, {}))

    assert "Branch: main" in output
    assert "Upstream: origin/main" in output
    assert not (project_path / ".git").exists()
    assert not executor.responses


def test_real_git_status_handles_unicode_newlines_and_stderr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = tmp_path / "repository"
    init_repository(repository)
    (repository / "café.txt").write_text("unicode\n", encoding="utf-8")
    paths = {"café.txt"}
    if os.name != "nt":
        (repository / "line\nbreak.txt").write_text("newline\n", encoding="utf-8")
        paths.add("line\nbreak.txt")
    empty_global_config = tmp_path / "gitconfig"
    empty_global_config.write_text("", encoding="utf-8")
    original_environment = git_executor.command_environment

    def test_environment(extra: dict[str, str] | None = None) -> dict[str, str]:
        environment = original_environment(extra)
        environment["GIT_CONFIG_GLOBAL"] = str(empty_global_config)
        return environment

    monkeypatch.setattr(git_executor, "command_environment", test_environment)
    executor = LocalGitExecutor(repository)

    place = GitPlace(repository, executor)
    status = in_worker(repository_status, place)
    assert {change.path for change in status.changes} == paths
    assert status.remote_url is None
    committed = in_worker(commit_all, place, "Save unusual paths")
    assert len(committed) == 7
    identity = asyncio.run(
        run_worktree_git(executor, ["log", "-1", "--format=%an <%ae>|%cn <%ce>"])
    ).stdout.strip()
    assert identity == "YAAP <yaap@localhost>|YAAP <yaap@localhost>"

    with pytest.raises(GitError, match="not a git command"):
        asyncio.run(run_worktree_git(executor, ["not-a-real-command"]))


@pytest.mark.skipif(os.name == "nt", reason="Git filter race uses POSIX shell semantics in the Linux runner.")
def test_filter_inserted_after_host_check_runs_only_through_target_executor(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    init_repository(repository)
    (repository / "content.txt").write_text("safe input\n", encoding="utf-8")
    check_repository(repository)
    marker = repository / "runner-canary.canary"
    executor = FilterRaceExecutor(repository, marker)

    asyncio.run(run_worktree_git(executor, ["add", "--all"]))

    assert marker.read_text(encoding="utf-8") == "ran"
    with pytest.raises(UnsafeRepository, match="filter.race.clean"):
        check_repository(repository)
