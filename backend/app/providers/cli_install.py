"""Installing the vendor CLIs (Claude Code, Codex) into the data folder at their first sign-in.

The image carries only the two files that name the CLIs: `tools/package.json` and `tools/package-lock.json`, with the
version and the integrity hash of every package. YAAP runs `npm ci` on them into `<data>/tools`, so the versions and
hashes are those of the YAAP release, and the CLIs are downloaded by the user's own YAAP, never shipped in an image.

A CLI counts as installed when its binary exists and the lock it was installed from is the lock this release ships.
A release with a newer lock therefore installs the new versions once, at the first start or the next sign-in. One
install runs at a time; a second caller waits for it.
"""

import asyncio
import contextlib
import logging
import os
import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import anyio

from app.core.background import BackgroundWork
from app.core.subprocess_env import safe_subprocess_env

logger = logging.getLogger(__name__)

InstallState = Literal["not_installed", "installing", "installed", "failed"]

TOOLS_FOLDER_VARIABLE = "YAAP_TOOLS_DIR"
SHIPPED_FOLDER_VARIABLE = "YAAP_TOOLS_SOURCE"
PACKAGE_FILE = "package.json"
LOCK_FILE = "package-lock.json"
# The lock the installed packages came from; written last, so an install that was cut off never counts.
INSTALLED_LOCK_FILE = "installed-package-lock.json"
CACHE_FOLDER = ".npm-cache"
INSTALL_TIMEOUT_SECONDS = 15 * 60.0
OUTPUT_TAIL = 400


class InstallFailed(Exception):
    """The CLIs could not be installed; the message is for the user."""


@dataclass(frozen=True)
class InstallStatus:
    state: InstallState
    message: str | None = None


def tools_folder() -> Path:
    """Where the CLIs are installed: the data folder's `tools`, named by the image."""
    return Path(os.environ.get(TOOLS_FOLDER_VARIABLE) or Path(tempfile.gettempdir()) / "yaap-tools")


def shipped_folder() -> Path:
    """The folder with the lock this release ships: `/app/tools` in the image, the repository's `tools` otherwise."""
    return Path(os.environ.get(SHIPPED_FOLDER_VARIABLE) or Path(__file__).resolve().parents[3] / "tools")


def tool_command(name: str) -> tuple[str, ...]:
    """The command that starts an installed CLI; its provider module passes this as the default command."""
    return (str(tools_folder() / "node_modules" / ".bin" / name),)


def installed_state(
    binary_present: bool, installed_lock: bytes | None, shipped_lock: bytes | None
) -> Literal["installed", "not_installed"]:
    """Installed only when the binary exists and was installed from the lock this release ships."""
    current = shipped_lock is not None and installed_lock == shipped_lock
    return "installed" if binary_present and current else "not_installed"


def sign_in_folders() -> tuple[Path, ...]:
    """The CLIs' own sign-in folders, named by the image. One that holds anything means the CLI was used before."""
    names = ("CLAUDE_CONFIG_DIR", "CODEX_HOME")
    return tuple(Path(value) for value in (os.environ.get(name) for name in names) if value)


def holds_files(folder: Path) -> bool:
    try:
        return folder.is_dir() and any(folder.iterdir())
    except OSError:
        return False


def read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


@dataclass
class CliTools:
    """The install of the CLIs: its state, and the one install that runs at a time."""

    folder: Path = field(default_factory=tools_folder)
    shipped: Path = field(default_factory=shipped_folder)
    # The npm command, replaceable in tests.
    npm: Sequence[str] = ("npm",)
    signed_in_folders: Sequence[Path] = field(default_factory=sign_in_folders)
    timeout: float = INSTALL_TIMEOUT_SECONDS
    work: BackgroundWork[None] = field(default_factory=BackgroundWork)
    task: asyncio.Task[None] | None = None
    failure: str | None = None

    def binary(self, name: str) -> Path:
        return self.folder / "node_modules" / ".bin" / name

    def is_installed(self, name: str) -> bool:
        state = installed_state(
            self.binary(name).exists(),
            read_bytes(self.folder / INSTALLED_LOCK_FILE),
            read_bytes(self.shipped / LOCK_FILE),
        )
        return state == "installed"

    def installing(self) -> bool:
        return self.task is not None and not self.task.done()

    def status(self, name: str) -> InstallStatus:
        if self.installing():
            return InstallStatus("installing")
        if self.is_installed(name):
            return InstallStatus("installed")
        if self.failure is not None:
            return InstallStatus("failed", self.failure)
        return InstallStatus("not_installed")

    def start(self) -> asyncio.Task[None]:
        """Start the install, or join the one that runs."""
        if self.task is not None and not self.task.done():
            return self.task
        self.failure = None
        self.task = self.work.start(self.install(), name="install-cli-tools")
        return self.task

    async def ensure(self, name: str) -> None:
        """Return when the CLI is installed and current; install first when it is not. Raises InstallFailed."""
        if self.is_installed(name):
            return
        task = self.start()
        # Waiting does not cancel the install when the caller is cancelled (a sign-in the user gave up on).
        await asyncio.wait({task})
        if not self.is_installed(name):
            raise InstallFailed(self.failure or "The installation did not finish. Try again.")

    def refresh_if_outdated(self) -> None:
        """At start: CLIs of an older release are installed again from this release's lock, and so are CLIs that an
        older image carried, when their sign-in folder shows they were used. Nothing is installed for a user who never
        signed in to one."""
        used = holds_files(self.folder / "node_modules" / ".bin") or any(map(holds_files, self.signed_in_folders))
        if used and read_bytes(self.folder / INSTALLED_LOCK_FILE) != read_bytes(self.shipped / LOCK_FILE):
            self.start()

    async def install(self) -> None:
        # A failure is kept for the status; nothing else waits on this task's result.
        try:
            await self.run_install()
        except InstallFailed as error:
            self.failure = str(error)
            logger.warning("Installing the CLIs failed: %s", error)
        except OSError as error:
            self.failure = f"The CLIs could not be installed: {error.strerror or error}"
            logger.warning("Installing the CLIs failed: %s", error)

    async def run_install(self) -> None:
        lock = read_bytes(self.shipped / LOCK_FILE)
        package = read_bytes(self.shipped / PACKAGE_FILE)
        if lock is None or package is None:
            raise InstallFailed("This YAAP does not carry the list of CLI versions (tools/package-lock.json).")
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / INSTALLED_LOCK_FILE).unlink(missing_ok=True)
        (self.folder / PACKAGE_FILE).write_bytes(package)
        (self.folder / LOCK_FILE).write_bytes(lock)
        logger.info("Installing the CLIs into %s", self.folder)
        output = await self.run_npm()
        shutil.rmtree(self.folder / CACHE_FOLDER, ignore_errors=True)
        if output is not None:
            raise InstallFailed(f"npm could not install the CLIs: {output}")
        (self.folder / INSTALLED_LOCK_FILE).write_bytes(lock)
        logger.info("The CLIs are installed")

    async def run_npm(self) -> str | None:
        """Run `npm ci`; None when it succeeded, otherwise the end of its output."""
        environment = safe_subprocess_env(
            {
                "npm_config_cache": str(self.folder / CACHE_FOLDER),
                "npm_config_update_notifier": "false",
                "npm_config_audit": "false",
                "npm_config_fund": "false",
            }
        )
        try:
            process = await asyncio.create_subprocess_exec(
                *self.npm,
                "ci",
                "--prefix",
                str(self.folder),
                cwd=self.folder,
                env=environment,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except FileNotFoundError:
            raise InstallFailed("npm is not available in this YAAP image.") from None
        try:
            output, _ = await asyncio.wait_for(process.communicate(), self.timeout)
        except TimeoutError:
            raise InstallFailed("The installation took too long and was stopped. Try again.") from None
        finally:
            await end_process(process)
        if process.returncode == 0:
            return None
        text = output.decode(errors="replace").strip()
        return text[-OUTPUT_TAIL:] or f"exit code {process.returncode}"

    async def stop(self) -> None:
        """At shutdown: end a running install."""
        await self.work.stop()


async def end_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
    # Shielded: the install is often ended by a cancelled scope, and the process must be reaped all the same.
    with anyio.CancelScope(shield=True):
        await process.wait()
