"""Where a language server runs and its process there.

A server runs where the project's commands run: in the environment's runner or the project's sandbox, over the same
SSH connection as `run_command` and the shell sessions, started with the fixed launcher of app/targets/stdio.py; with
the runner switched off (development, the test suite) as a local process with `safe_subprocess_env()`. Its standard
error is kept apart from the protocol on standard output and its last lines are shown when it fails.
"""

import asyncio
import collections
import contextlib
import shlex
import sys
from dataclasses import dataclass
from typing import Protocol

import asyncssh

from app.core.subprocess_env import safe_subprocess_env
from app.lsp.connection import ByteReader, ByteWriter, LspError, LspUnavailable
from app.lsp.paths import PathMap
from app.targets.base import TargetError
from app.targets.container import stop_tree
from app.targets.ssh import SshTarget
from app.targets.stdio import LAUNCHER, exec_line, exec_script

STDERR_LINES = 40
# The exit status of a shell whose `exec` found no such program.
NOT_FOUND = 127
PROBE_SECONDS = 15
LOCAL = "container"


class ServerProcess(Protocol):
    stdout: ByteReader
    stdin: ByteWriter

    @property
    def alive(self) -> bool: ...

    @property
    def exit_code(self) -> int | None: ...

    def stderr_tail(self) -> str: ...

    async def wait(self, timeout: float) -> None: ...

    def kill(self) -> None: ...


class StderrTail:
    """The last lines a server wrote to standard error, read in a task of their own."""

    def __init__(self) -> None:
        self.lines: collections.deque[str] = collections.deque(maxlen=STDERR_LINES)
        self.task: asyncio.Task[None] | None = None

    def follow(self, reader: ByteReader) -> None:
        async def read() -> None:
            with contextlib.suppress(OSError, asyncssh.Error, ValueError):
                while line := await reader.readline():
                    self.lines.append(line.decode("utf-8", errors="replace").rstrip()[:500])

        self.task = asyncio.create_task(read())

    def text(self) -> str:
        return "\n".join(self.lines)

    def cancel(self) -> None:
        if self.task is not None:
            self.task.cancel()


class LocalProcess:
    def __init__(self, process: asyncio.subprocess.Process) -> None:
        assert process.stdout is not None and process.stdin is not None and process.stderr is not None
        self.process = process
        self.stdout: ByteReader = process.stdout
        self.stdin: ByteWriter = process.stdin
        self.stderr = StderrTail()
        self.stderr.follow(process.stderr)

    @property
    def alive(self) -> bool:
        return self.process.returncode is None

    @property
    def exit_code(self) -> int | None:
        return self.process.returncode

    def stderr_tail(self) -> str:
        return self.stderr.text()

    async def wait(self, timeout: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.process.wait(), timeout)

    def kill(self) -> None:
        if self.process.returncode is None:
            stop_tree(self.process)
        self.stderr.cancel()


class RemoteProcess:
    def __init__(self, process: asyncssh.SSHClientProcess[bytes]) -> None:
        self.process = process
        self.stdout: ByteReader = process.stdout
        self.stdin: ByteWriter = process.stdin
        self.stderr = StderrTail()
        self.stderr.follow(process.stderr)

    @property
    def alive(self) -> bool:
        return self.process.exit_status is None and not self.process.is_closing()

    @property
    def exit_code(self) -> int | None:
        return self.process.exit_status

    def stderr_tail(self) -> str:
        return self.stderr.text()

    async def wait(self, timeout: float) -> None:
        with contextlib.suppress(TimeoutError, asyncssh.Error, OSError):
            await asyncio.wait_for(self.process.wait_closed(), timeout)

    def kill(self) -> None:
        # Closing the channel ends the server and what it started.
        self.process.close()
        self.stderr.cancel()


@dataclass(frozen=True)
class Place:
    """Where the servers of one project run, and the project folder as they see it."""

    # The machine: "container" for YAAP's own process, else the SSH connection's key ("target-<id>").
    key: str
    paths: PathMap
    # The runner or sandbox, reached over SSH; None runs the server as a local process.
    remote: SshTarget | None = None

    @property
    def root(self) -> str:
        return self.paths.root

    def note_use(self) -> None:
        """A use of the server keeps its runner or sandbox from being stopped as idle."""
        if self.remote is not None:
            self.remote.note_use()

    async def spawn(self, command: tuple[str, ...]) -> ServerProcess:
        if self.remote is None:
            return await spawn_local(command, self.root)
        return await spawn_remote(self.remote, command, self.root)

    async def probe(self, programs: list[str]) -> set[str]:
        """Which of these programs are installed there (only asked of a runner or sandbox)."""
        if self.remote is None or not programs:
            return set()
        try:
            connection = await self.remote.connection()
            result = await connection.run(
                "command -v " + " ".join(shlex.quote(program) for program in programs),
                check=False,
                timeout=PROBE_SECONDS,
            )
        except (TargetError, asyncssh.Error, OSError, TimeoutError):
            return set()
        found = str(result.stdout or "").split()
        return {program for program in programs if any(line.rsplit("/", 1)[-1] == program for line in found)}


async def spawn_local(command: tuple[str, ...], cwd: str) -> LocalProcess:
    options: dict[str, object] = {}
    if sys.platform != "win32":
        # Its own process group, so stopping it also stops what it started.
        options["start_new_session"] = True
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=safe_subprocess_env(),
            cwd=cwd,
            **options,  # type: ignore[arg-type]  # start_new_session only exists off Windows
        )
    except FileNotFoundError as error:
        raise LspUnavailable(f"{command[0]} is not installed here.") from error
    except OSError as error:
        raise LspError(f"Could not start {command[0]}: {error}") from error
    return LocalProcess(process)


async def spawn_remote(target: SshTarget, command: tuple[str, ...], cwd: str) -> RemoteProcess:
    try:
        connection = await target.connection()
        process = await connection.create_process(LAUNCHER, encoding=None)
    except (TargetError, asyncssh.Error, OSError) as error:
        raise LspError(f"Could not start {command[0]} where the project's commands run: {error}") from error
    process.stdin.write(exec_line(exec_script(command, (), cwd)))
    return RemoteProcess(process)
