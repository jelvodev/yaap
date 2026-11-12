"""Where the agent works: a file system confined to a working folder, and an executor for commands.

The container (container.py) and a machine over SSH (ssh.py) implement the same interface, so the tools work the
same on every target. Paths are relative to the working folder, with / as separator.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from app.targets.sessions import ShellState


class TargetError(Exception):
    """A file or command action the target refuses or cannot do; the message goes back to the model."""


@dataclass(frozen=True)
class FileEntry:
    # Relative to the working folder; "." is the folder itself.
    path: str
    is_dir: bool
    size: int
    # Last change as a Unix timestamp, when the target reports it (the Files tab shows it).
    modified: float | None = None


@dataclass(frozen=True)
class CommandResult:
    exit_code: int | None
    # Everything the command printed, also when it was stopped.
    output: str
    timed_out: bool = False
    # Why it was stopped: the total time ran out, or it printed nothing for the silence time.
    stopped: Literal["total", "silence"] | None = None


@dataclass(frozen=True)
class CapturedResult:
    """A finished command with its two output streams apart and undecoded, for output that is parsed or binary."""

    exit_code: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool = False


class TargetFiles(Protocol):
    """Files inside one working folder. Every path is checked to stay inside it."""

    def check(self, path: str, for_write: bool = False) -> str:
        """The normalized relative path, or TargetError when it leaves the folder (or writes into .git)."""
        ...

    async def stat(self, path: str) -> FileEntry | None: ...

    async def read_bytes(self, path: str) -> bytes: ...

    async def write_bytes(self, path: str, data: bytes) -> None:
        """Write the file, creating its folders."""
        ...

    async def remove(self, path: str) -> None:
        """Delete a file; nothing happens when it is gone already."""
        ...

    async def walk(self, path: str, recursive: bool, skip: set[str], limit: int) -> list[FileEntry]:
        """Entries below a folder, folders first per level; stops after `limit` entries."""
        ...

    async def search(self, pattern: str, path: str, glob: str, limit: int, skip: set[str] | None = None) -> list[str]:
        """Lines matching a regular expression, as "path:line: text"."""
        ...


class CommandExecutor(Protocol):
    """Runs commands with the working folder as current folder."""

    async def run(
        self,
        command: str,
        env: dict[str, str],
        timeout: float,
        session: ShellState | None = None,
        silence: float | None = None,
        on_output: Callable[[str], None] | None = None,
    ) -> CommandResult:
        """Run a shell command (in a shell session when one is given, sessions.py) and wait for it. The output is
        read while it runs and passed to on_output; after `timeout` seconds, or `silence` seconds without output, the
        command and what it started are stopped and the output so far is returned."""
        ...

    async def spawn(
        self, command: str, env: dict[str, str], session: ShellState | None = None
    ) -> asyncio.subprocess.Process:
        """Start a command that keeps running, with its output on stdout, in its own process group."""
        ...

    async def capture(self, command: str, timeout: float) -> CapturedResult:
        """Run a shell command without a shell session or secrets and wait for it. Standard output and standard
        error are kept apart as bytes; after `timeout` seconds the command is stopped (timed_out, no output)."""
        ...


class Target(Protocol):
    kind: str  # "container", later "ssh" and "sandbox"

    def files(self, root: str) -> TargetFiles: ...

    def executor(self, root: str) -> CommandExecutor: ...
