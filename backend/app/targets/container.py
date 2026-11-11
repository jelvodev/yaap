"""The container target: files on YAAP's own disk under /workspace, and commands in a local shell."""

import asyncio
import codecs
import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

import anyio

from app.config import environment_value
from app.targets.base import CommandResult, FileEntry, TargetError
from app.targets.safe_root import SafeRoot, SafeRootError, relative_parts
from app.targets.sessions import ShellState, bash_session

MAX_SEARCH_FILE_BYTES = 1024 * 1024
# A command's output is kept up to this size; beyond it, its start and its newest part.
MAX_COLLECTED_CHARS = 2_000_000
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")


def stop_tree(process: asyncio.subprocess.Process) -> None:
    """Stop the command and everything it started."""
    try:
        if sys.platform != "win32":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True, check=False)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        process.kill()
    except ProcessLookupError:
        pass


class Collected:
    """Output kept within MAX_COLLECTED_CHARS: the start, and the newest part after it."""

    def __init__(self) -> None:
        self.head = ""
        self.tail = ""
        self.dropped = 0

    def add(self, text: str) -> None:
        if len(self.head) < MAX_COLLECTED_CHARS // 2:
            room = MAX_COLLECTED_CHARS // 2 - len(self.head)
            self.head += text[:room]
            text = text[room:]
        self.tail += text
        if len(self.tail) > MAX_COLLECTED_CHARS // 2:
            cut = len(self.tail) - MAX_COLLECTED_CHARS // 2
            self.dropped += cut
            self.tail = self.tail[cut:]

    def text(self) -> str:
        gap = f"\n[… {self.dropped:,} characters of output not kept …]\n" if self.dropped else ""
        return ANSI.sub("", self.head + gap + self.tail).replace("\r\n", "\n")


def bash_path() -> str | None:
    return environment_value("YAAP_BASH") or shutil.which("bash")


def require_bash() -> str:
    bash = bash_path()
    if bash is None:
        raise TargetError("Shell sessions need bash, which is not installed here. Run the command without a session.")
    return bash


def session_script(session: ShellState, command: str) -> str:
    """The bash script for a command in a session whose state lives in a folder under data/shells/."""
    if session.folder is None:
        raise TargetError("This shell session has no state folder.")
    session.folder.mkdir(parents=True, exist_ok=True)
    return bash_session(shlex.quote((session.folder / "state").as_posix()), command, session.save)


def local_entry(relative: str, path: Path, is_dir: bool | None = None) -> FileEntry:
    """An entry with its size and last change; a file that vanished or cannot be read keeps size 0."""
    try:
        info = path.lstat()
    except OSError:
        return FileEntry(relative, bool(is_dir), 0)
    folder = stat.S_ISDIR(info.st_mode) if is_dir is None else is_dir
    return FileEntry(relative, folder, 0 if folder or not stat.S_ISREG(info.st_mode) else info.st_size, info.st_mtime)


class LocalFiles:
    def __init__(self, root: str) -> None:
        self.root = Path(os.path.abspath(root))
        self.safe = SafeRoot(self.root)

    def absolute(self, path: str) -> Path:
        return self.root.joinpath(*relative_parts(path or "."))

    def relative(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix() or "."
        except ValueError as error:
            raise TargetError(f"{path} is outside the project folder.") from error

    def check(self, path: str, for_write: bool = False) -> str:
        try:
            parts = relative_parts(path or ".")
        except SafeRootError as error:
            raise TargetError(f"{path} is outside the project folder. Use a path relative to the project.") from error
        if for_write:
            try:
                canonical = self.safe.canonical_parts("/".join(parts)) if parts else ()
            except SafeRootError as error:
                if "replaced" in str(error).casefold():
                    raise TargetError("The project folder was replaced.") from error
                raise TargetError(f"The path {path} cannot be written safely.") from error
            git_parts = {part.casefold() for part in canonical} if os.name == "nt" else set(canonical)
            if not parts or ".git" in git_parts:
                raise TargetError("Writing into .git or replacing the project folder itself is not allowed.")
        return "/".join(parts) or "."

    async def stat(self, path: str) -> FileEntry | None:
        relative = self.check(path)
        try:
            info = self.safe.stat(relative)
        except OSError as error:
            raise TargetError(f"Could not inspect {path}: {error}") from error
        if info is None:
            return None
        is_directory = stat.S_ISDIR(info.st_mode)
        size = info.st_size if stat.S_ISREG(info.st_mode) else 0
        return FileEntry(relative, is_directory, size, info.st_mtime)

    async def read_bytes(self, path: str) -> bytes:
        relative = self.check(path)
        try:
            return await anyio.to_thread.run_sync(self.safe.read_bytes, relative)
        except OSError as error:
            raise TargetError(f"Could not read {path}: {error}") from error

    async def write_bytes(self, path: str, data: bytes) -> None:
        relative = self.check(path, for_write=True)
        try:
            await anyio.to_thread.run_sync(self.safe.write_bytes, relative, data)
        except OSError as error:
            raise TargetError(f"Could not write {path}: {error}") from error

    async def remove(self, path: str) -> None:
        relative = self.check(path, for_write=True)
        try:
            await anyio.to_thread.run_sync(self.safe.remove, relative)
        except OSError as error:
            raise TargetError(f"Could not remove {path}: {error}") from error

    async def walk(self, path: str, recursive: bool, skip: set[str], limit: int) -> list[FileEntry]:
        relative = self.check(path)

        def collect() -> list[FileEntry]:
            entries: list[FileEntry] = []
            try:
                found = self.safe.entries(relative, recursive, skip)
                for entry_path, info in found:
                    folder_entry = stat.S_ISDIR(info.st_mode)
                    size = info.st_size if stat.S_ISREG(info.st_mode) else 0
                    entries.append(FileEntry(entry_path, folder_entry, size, info.st_mtime))
                    if len(entries) >= limit:
                        break
            except OSError as error:
                raise TargetError(f"Could not list {path}: {error}") from error
            if not recursive:
                return entries
            return entries

        return await anyio.to_thread.run_sync(collect)

    async def search(self, pattern: str, path: str, glob: str, limit: int, skip: set[str] | None = None) -> list[str]:
        compiled = re.compile(pattern)
        relative = self.check(path)
        skipped = skip or set()

        def scan() -> list[str]:
            matches: list[str] = []
            try:
                for name, info in self.safe.entries(relative, True, skipped):
                    if stat.S_ISDIR(info.st_mode):
                        continue
                    if not PurePosixPath(name).match(glob or "*"):
                        continue
                    try:
                        descriptor = self.safe.open_file(name)
                        with os.fdopen(descriptor, "rb") as stream:
                            opened = os.fstat(stream.fileno())
                            if not stat.S_ISREG(opened.st_mode) or opened.st_size > MAX_SEARCH_FILE_BYTES:
                                continue
                            data = stream.read(MAX_SEARCH_FILE_BYTES + 1)
                        if len(data) > MAX_SEARCH_FILE_BYTES:
                            continue
                        text = data.decode("utf-8")
                    except (UnicodeDecodeError, OSError):
                        continue
                    for number, line in enumerate(text.splitlines(), 1):
                        if compiled.search(line):
                            matches.append(f"{name}:{number}: {line.strip()[:300]}")
                            if len(matches) >= limit:
                                return matches
            except (UnicodeDecodeError, OSError):
                return matches
            return matches

        return await anyio.to_thread.run_sync(scan)


class LocalExecutor:
    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()

    def options(self, env: dict[str, str]) -> dict[str, Any]:
        return {
            "cwd": self.root,
            "env": env,
            "stdin": asyncio.subprocess.DEVNULL,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.STDOUT,
        }

    async def start(
        self, command: str, options: dict[str, Any], session: ShellState | None
    ) -> asyncio.subprocess.Process:
        if session is not None:
            return await asyncio.create_subprocess_exec(require_bash(), "-c", session_script(session, command), **options)
        return await asyncio.create_subprocess_shell(command, **options)

    async def run(
        self,
        command: str,
        env: dict[str, str],
        timeout: float,
        session: ShellState | None = None,
        silence: float | None = None,
        on_output: Callable[[str], None] | None = None,
    ) -> CommandResult:
        options = self.options(env)
        if sys.platform != "win32":
            options["start_new_session"] = True
        process = await self.start(command, options, session)
        assert process.stdout is not None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        collected = Collected()
        stopped: str | None = None
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                stopped = "total"
                break
            wait = min(remaining, silence) if silence else remaining
            try:
                chunk = await asyncio.wait_for(process.stdout.read(65536), timeout=wait)
            except TimeoutError:
                stopped = "silence" if silence and silence < remaining else "total"
                break
            if not chunk:
                break
            text = decoder.decode(chunk)
            if text:
                collected.add(text)
                if on_output is not None:
                    on_output(ANSI.sub("", text))
        collected.add(decoder.decode(b"", final=True))
        if stopped is not None:
            stop_tree(process)
            await process.wait()
            return CommandResult(None, collected.text(), timed_out=True, stopped=stopped)  # type: ignore[arg-type]
        await process.wait()
        return CommandResult(process.returncode, collected.text())

    async def spawn(
        self, command: str, env: dict[str, str], session: ShellState | None = None
    ) -> asyncio.subprocess.Process:
        options = self.options(env)
        if sys.platform != "win32":
            # Its own process group, so stopping it also stops what it started (a dev server's workers).
            options["start_new_session"] = True
        return await self.start(command, options, session)


class ContainerTarget:
    kind = "container"

    def files(self, root: str) -> LocalFiles:
        return LocalFiles(root)

    def executor(self, root: str) -> LocalExecutor:
        return LocalExecutor(root)


CONTAINER = ContainerTarget()
