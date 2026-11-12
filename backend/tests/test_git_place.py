import asyncio
import subprocess
from collections.abc import Callable
from pathlib import Path

import anyio
import asyncssh
import pytest

from app.git import remote as git_remote
from app.git import service
from app.git.common import GitResult
from app.git.place import GitPlace, git_place, git_place_kind
from app.git.service import GitError, archive, run_git
from app.runner.service import RunnerUnavailable, Runners
from app.targets.base import CapturedResult, TargetError
from app.targets.ssh import SshExecutor, SshSettings


class FakeExecutor:
    """Records the commands and answers with the next prepared result."""

    def __init__(self, *results: CapturedResult) -> None:
        self.results = list(results) or [CapturedResult(0, b"", b"")]
        self.commands: list[str] = []

    async def capture(self, command: str, timeout: float) -> CapturedResult:
        self.commands.append(command)
        return self.results.pop(0) if len(self.results) > 1 else self.results[0]


def in_worker(function: Callable[..., object], *arguments: object) -> object:
    """Call a sync git function the way a router does: from a worker thread of the event loop."""

    async def call() -> object:
        return await anyio.to_thread.run_sync(function, *arguments)

    return asyncio.run(call())


def fail_when_called(*_arguments: object, **_keywords: object) -> None:
    raise AssertionError("YAAP started a local process")


@pytest.mark.parametrize(
    ("runner_enabled", "has_workspace", "folder", "expected"),
    [
        (False, True, "workspace", "yaap"),
        (True, False, "workspace", "yaap"),
        (True, True, "workspace", "runner"),
        (True, True, "workspace/env/site", "runner"),
        (True, True, "elsewhere/site", "yaap"),
        (True, True, "workspace2/site", "yaap"),
        (True, True, "workspace/../elsewhere", "yaap"),
        (True, True, "workspace/env/../../outside", "yaap"),
        (True, True, "workspace/env/../site", "runner"),
    ],
)
def test_git_place_kind_sends_only_folders_in_the_workspace_to_the_runner(
    tmp_path: Path, runner_enabled: bool, has_workspace: bool, folder: str, expected: str
) -> None:
    workspace = tmp_path / "workspace"
    assert git_place_kind(tmp_path / folder, workspace if has_workspace else None, runner_enabled) == expected


def test_git_place_kind_resolves_a_relative_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    assert git_place_kind(Path("site"), workspace, True) == "runner"
    assert git_place_kind(Path("..") / "other", workspace, True) == "yaap"


class FakeRuntime:
    def __init__(self, executor: FakeExecutor | None, error: TargetError | None = None) -> None:
        self.provided = executor
        self.error = error
        self.roots: list[str] = []

    def executor(self, root: str) -> FakeExecutor:
        self.roots.append(root)
        if self.error is not None:
            raise self.error
        assert self.provided is not None
        return self.provided


def test_git_place_outside_the_workspace_is_yaaps_own_process(tmp_path: Path) -> None:
    runners = Runners(enabled=True, workspace_dir=tmp_path / "workspace")

    assert git_place(1, tmp_path / "blueprint", runners) == GitPlace(tmp_path / "blueprint")


def test_git_place_in_the_workspace_uses_the_executor_of_the_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runners = Runners(enabled=True, workspace_dir=tmp_path / "workspace")
    executor = FakeExecutor()
    runtime = FakeRuntime(executor)
    monkeypatch.setattr(Runners, "runtime", lambda self, environment_id, used_by: runtime)
    folder = tmp_path / "workspace" / "env" / "site"

    place = git_place(1, folder, runners)

    assert place.folder == folder and place.executor is executor
    assert runtime.roots == [str(folder)]


@pytest.mark.parametrize(
    "failure",
    [RunnerUnavailable("The runner cannot start."), TargetError("That folder is outside the environment.")],
)
def test_git_place_fails_closed_when_the_runner_cannot_be_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: TargetError
) -> None:
    runners = Runners(enabled=True, workspace_dir=tmp_path / "workspace")
    monkeypatch.setattr(service.subprocess, "run", fail_when_called)
    monkeypatch.setattr(Runners, "runtime", lambda self, environment_id, used_by: FakeRuntime(None, failure))

    with pytest.raises(GitError, match=str(failure)):
        git_place(1, tmp_path / "workspace" / "env" / "site", runners)


def test_git_place_fails_closed_when_the_runners_are_not_started(tmp_path: Path) -> None:
    runners = Runners(enabled=True, workspace_dir=tmp_path / "workspace")

    with pytest.raises(GitError, match="has not started its runners"):
        git_place(1, tmp_path / "workspace" / "env" / "site", runners)


def test_run_git_on_a_place_runs_through_the_executor_and_starts_no_local_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service.subprocess, "run", fail_when_called)
    executor = FakeExecutor(CapturedResult(0, b"result\n", b""))

    result = in_worker(run_git, ["rev-parse", "HEAD"], GitPlace(tmp_path, executor))

    assert isinstance(result, GitResult) and result.stdout == "result\n"
    assert len(executor.commands) == 1 and "rev-parse HEAD" in executor.commands[0]


def test_run_git_on_a_place_keeps_stdout_and_stderr_apart(tmp_path: Path) -> None:
    executor = FakeExecutor(CapturedResult(0, "café\n".encode(), b"warning: something\n"))

    result = in_worker(run_git, ["status"], GitPlace(tmp_path, executor))

    assert (result.stdout, result.stderr) == ("café\n", "warning: something\n")  # type: ignore[attr-defined]


def test_run_git_on_a_place_reports_stderr_of_a_failed_command(tmp_path: Path) -> None:
    executor = FakeExecutor(CapturedResult(128, b"progress\n", b"fatal: not a repository\n"))

    with pytest.raises(GitError, match="git status failed: fatal: not a repository"):
        in_worker(run_git, ["status"], GitPlace(tmp_path, executor))


def test_run_git_on_a_place_accepts_the_given_exit_codes(tmp_path: Path) -> None:
    executor = FakeExecutor(CapturedResult(1, b"differences\n", b""))

    result = in_worker(run_git, ["diff"], GitPlace(tmp_path, executor), None, (), 30, (0, 1))

    assert result.exit_code == 1  # type: ignore[attr-defined]


def test_run_git_on_a_place_reports_a_timeout(tmp_path: Path) -> None:
    executor = FakeExecutor(CapturedResult(None, b"", b"", timed_out=True))

    with pytest.raises(GitError, match="longer than 5 seconds"):
        in_worker(run_git, ["fetch-like"], GitPlace(tmp_path, executor), None, (), 5)


@pytest.mark.parametrize("command", ["fetch", "push", "pull", "clone", "ls-remote", "update"])
def test_network_commands_are_refused_on_a_place_with_an_executor(tmp_path: Path, command: str) -> None:
    executor = FakeExecutor()

    with pytest.raises(GitError, match="run in YAAP"):
        run_git([command, "origin"], GitPlace(tmp_path, executor))

    assert executor.commands == []


def test_a_credential_is_refused_on_a_place_with_an_executor(tmp_path: Path) -> None:
    executor = FakeExecutor()

    with pytest.raises(GitError, match="run in YAAP"):
        run_git(["status"], GitPlace(tmp_path, executor), private_config=(("http.extraheader", "secret"),))

    assert executor.commands == []


def test_run_git_on_a_place_is_refused_on_the_event_loop_thread(tmp_path: Path) -> None:
    executor = FakeExecutor()

    async def call_on_the_loop() -> None:
        run_git(["status"], GitPlace(tmp_path, executor))

    with pytest.raises(RuntimeError, match="worker thread"):
        asyncio.run(call_on_the_loop())

    assert executor.commands == []


def test_run_git_on_a_place_outside_an_event_loop_needs_an_anyio_worker_thread(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        run_git(["status"], GitPlace(tmp_path, FakeExecutor()))


def test_archive_through_an_executor_returns_the_exact_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service.subprocess, "run", fail_when_called)
    payload = bytes(range(256)) * 3
    executor = FakeExecutor(CapturedResult(0, payload, b"noise on stderr"))

    data = in_worker(archive, GitPlace(tmp_path, executor), "abc1234", ["docs", "a b.md"])

    assert data == payload
    assert "archive --format=tar abc1234 -- docs 'a b.md'" in executor.commands[0]


def test_archive_through_an_executor_reports_the_error_of_git(tmp_path: Path) -> None:
    executor = FakeExecutor(CapturedResult(128, b"", b"fatal: not a valid object name\n"))

    with pytest.raises(GitError, match="git archive failed: fatal: not a valid object name"):
        in_worker(archive, GitPlace(tmp_path, executor), "abc1234", [])


class Calls:
    """Stands in for run_git in the remote module and records where each command ran."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], object]] = []

    def __call__(self, arguments: list[str], repository: object, **_options: object) -> GitResult:
        self.calls.append((list(arguments), repository))
        if arguments[:2] == ["remote", "get-url"]:
            return GitResult("https://github.com/acme/site.git\n", "")
        if arguments[:2] == ["rev-parse", "--verify"]:
            return GitResult("", "", 0)
        if arguments[:1] == ["symbolic-ref"]:
            return GitResult("main\n", "")
        return GitResult("", "")


def test_pull_on_a_place_fetches_in_yaap_and_merges_on_the_executor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = Calls()
    monkeypatch.setattr(git_remote, "run_git", calls)
    place = GitPlace(tmp_path, FakeExecutor())

    git_remote.pull(place, "token")

    where = {arguments[0]: repository for arguments, repository in calls.calls}
    assert where["remote"] is place
    assert where["fetch"] == tmp_path
    assert where["merge"] is place


def test_push_on_a_place_reads_the_state_on_the_executor_and_pushes_in_yaap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = Calls()
    place = GitPlace(tmp_path, FakeExecutor())
    monkeypatch.setattr(git_remote, "run_git", calls)
    monkeypatch.setattr(
        git_remote,
        "repository_status",
        lambda repository: service.parse_repository_status("# branch.oid abc\n# branch.head main\n"),
    )

    git_remote.push(place, "token")

    pushes = [repository for arguments, repository in calls.calls if arguments[0] == "push"]
    assert pushes == [tmp_path]
    assert [repository for arguments, repository in calls.calls if arguments[0] == "remote"] == [place]


def test_clone_with_a_checkout_place_clones_without_files_and_checks_out_on_the_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = Calls()
    monkeypatch.setattr(git_remote, "run_git", calls)
    target = tmp_path / "site"
    place = GitPlace(target, FakeExecutor())

    git_remote.clone_repository("https://github.com/acme/site.git", target, "token", place)

    (clone, clone_where), *after = calls.calls
    assert "--no-checkout" in clone and clone_where == tmp_path
    assert all(repository is place for _arguments, repository in after)
    assert after[-1][0] == ["checkout", "--force", "main"]


def test_clone_without_a_checkout_place_checks_out_in_the_clone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = Calls()
    monkeypatch.setattr(git_remote, "run_git", calls)

    git_remote.clone_repository("https://github.com/acme/site.git", tmp_path / "site", None)

    assert len(calls.calls) == 1 and "--no-checkout" not in calls.calls[0][0]


def test_clone_with_no_checkout_then_a_forced_checkout_writes_the_files(tmp_path: Path) -> None:
    def host_git(*arguments: str, cwd: Path) -> None:
        subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@example.com", *arguments], cwd=cwd, check=True)

    source = tmp_path / "source"
    source.mkdir()
    host_git("init", "-b", "main", cwd=source)
    (source / "file.txt").write_text("content\n", encoding="utf-8")
    host_git("add", ".", cwd=source)
    host_git("commit", "-m", "First", cwd=source)
    host_git("clone", "--no-checkout", str(source), "copy", cwd=tmp_path)
    assert not (tmp_path / "copy" / "file.txt").exists()

    host_git("checkout", "--force", "main", cwd=tmp_path / "copy")

    assert (tmp_path / "copy" / "file.txt").read_text(encoding="utf-8") == "content\n"
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=tmp_path / "copy", capture_output=True, text=True, check=True
    )
    assert status.stdout == ""


class FakeStream:
    def __init__(self, data: bytes, delay: float = 0.0) -> None:
        self.data = data
        self.delay = delay

    async def read(self) -> bytes:
        await asyncio.sleep(self.delay)
        return self.data


class FakeStdin:
    def __init__(self) -> None:
        self.written = b""

    def write(self, data: bytes) -> None:
        self.written += data

    def write_eof(self) -> None:
        pass


class FakeProcess:
    def __init__(self, stdout: bytes, stderr: bytes, exit_status: int, delay: float = 0.0) -> None:
        self.stdin = FakeStdin()
        self.stdout = FakeStream(stdout, delay)
        self.stderr = FakeStream(stderr, delay)
        self.exit_status = exit_status
        self.closed = False

    async def wait(self, check: bool = True) -> "FakeProcess":
        return self

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    def __init__(self, process: FakeProcess) -> None:
        self.process = process
        self.options: dict[str, object] = {}

    async def create_process(self, command: str, **options: object) -> FakeProcess:
        self.options = options
        return self.process


class FakeSshTarget:
    def __init__(self, connection: FakeConnection) -> None:
        self.settings = SshSettings("runner", 22, "yaap", "key", "host-key")
        self.target_connection = connection

    async def connection(self) -> FakeConnection:
        return self.target_connection


def test_ssh_capture_keeps_the_streams_apart_as_bytes() -> None:
    process = FakeProcess(b"\xff\x00binary", b"warning\n", 3)
    connection = FakeConnection(process)
    executor = SshExecutor(FakeSshTarget(connection), "/workspace/site")  # type: ignore[arg-type] # fake target

    result = asyncio.run(executor.capture("git status", 5))

    assert result == CapturedResult(3, b"\xff\x00binary", b"warning\n")
    assert connection.options["encoding"] is None and connection.options["stderr"] == asyncssh.PIPE
    assert b"cd '/workspace/site'" in process.stdin.written and b"git status" in process.stdin.written


def test_ssh_capture_stops_the_command_after_the_timeout() -> None:
    process = FakeProcess(b"late", b"", 0, delay=5)
    target = FakeSshTarget(FakeConnection(process))
    executor = SshExecutor(target, "/workspace/site")  # type: ignore[arg-type] # fake target

    result = asyncio.run(executor.capture("sleep 100", 0.05))

    assert result.timed_out and result.exit_code is None
    assert process.closed
