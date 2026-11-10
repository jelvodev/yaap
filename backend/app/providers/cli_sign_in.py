"""Signing in to a subscription CLI (Claude Code, Codex) from the browser instead of a terminal.

YAAP starts the CLI's own sign-in command without a terminal and reads the sign-in link from its output. The user
signs in at the vendor in their own browser. Claude Code then shows a code that is pasted back and written to the
process's input; Codex waits by itself until the user has entered its one-time code at the vendor. YAAP never sees
the credentials: the CLI keeps them in its own sign-in folder.

At most one sign-in process runs per CLI. The process output and the pasted code are never logged; only the sign-in
link (and Codex's one-time code, which is meant to be shown) reaches the client.
"""

import asyncio
import contextlib
import logging
import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import anyio

from app.core.background import BackgroundWork
from app.providers.cli_install import CliTools, InstallFailed

logger = logging.getLogger(__name__)

SignInState = Literal[
    "idle", "installing", "starting", "waiting_for_code", "waiting_for_browser", "signed_in", "failed", "cancelled"
]
FINISHED_STATES: frozenset[SignInState] = frozenset({"signed_in", "failed", "cancelled"})

LINK_TIMEOUT_SECONDS = 30.0
# The vendors' codes live for about 15 minutes.
TOTAL_TIMEOUT_SECONDS = 15 * 60.0
MAX_CODE_LENGTH = 512
OUTPUT_LINE_LIMIT = 64 * 1024

ANSI_PATTERN = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")
LINK_PATTERN = re.compile(r"https?://[^\s\"'<>]+")
HTTPS_PORT = 443


@dataclass(frozen=True)
class SignInSpec:
    """How to sign in to one CLI. The vendor's knowledge (command, hosts, output format) sits in its provider module."""

    # The name used in the API path.
    key: str
    display_name: str
    command: tuple[str, ...]
    environment: Callable[[], dict[str, str]]
    working_directory: Callable[[], Path]
    # The only hosts the sign-in link may point to.
    allowed_hosts: frozenset[str]
    # Whether the user pastes a code back (Claude Code) or the CLI waits for the browser by itself (Codex).
    paste_code: bool
    # Finds the one-time code in the output when the CLI shows one for the user to enter at the vendor.
    user_code_pattern: re.Pattern[str] | None
    # Whether the CLI reports a sign-in, checked when the process has ended.
    signed_in: Callable[[], Awaitable[bool]]
    # Output that shows the CLI refused the pasted code and waits for another one.
    refused_code_pattern: re.Pattern[str] | None = None
    # The installed program (its name in the tools folder) that is installed first when it is missing or outdated; None
    # for a CLI that needs no install.
    tool: str | None = None


@dataclass(frozen=True)
class SignInStatus:
    state: SignInState
    url: str | None = None
    user_code: str | None = None
    code_sent: bool = False
    message: str | None = None


class NoSignIn(Exception):
    """There is no sign-in waiting for this."""


class CodeRefused(Exception):
    """The pasted code is not acceptable."""


class UnknownCli(Exception):
    """No sign-in is defined for this CLI."""


class SignInFailed(Exception):
    """The sign-in cannot go on; the message is for the user."""


def strip_ansi(text: str) -> str:
    """The text without terminal colour and cursor codes."""
    return ANSI_PATTERN.sub("", text)


def find_link(text: str) -> str | None:
    """The first web address in the text, without the punctuation that ends a sentence."""
    match = LINK_PATTERN.search(text)
    return match.group(0).rstrip(".,;:)]") if match else None


def link_is_allowed(link: str, allowed_hosts: frozenset[str]) -> bool:
    """Whether the link is plain https to one of the expected hosts, without credentials in it."""
    try:
        parts = urlsplit(link)
        port = parts.port
    except ValueError:
        return False
    if parts.scheme != "https" or parts.username is not None or parts.password is not None:
        return False
    if port not in (None, HTTPS_PORT):
        return False
    return (parts.hostname or "").lower() in allowed_hosts


def clean_code(raw: str) -> str:
    """The pasted code without the whitespace around it. Raises CodeRefused when it cannot be a code: empty, too long
    or containing whitespace, control characters or line breaks inside."""
    code = raw.strip()
    if not code:
        raise CodeRefused("Paste the code that the sign-in page shows.")
    if len(code) > MAX_CODE_LENGTH:
        raise CodeRefused("That is too long to be a sign-in code.")
    if any(character.isspace() or not character.isprintable() for character in code):
        raise CodeRefused("A sign-in code has no spaces or line breaks. Paste only the code.")
    return code


def find_user_code(text: str, pattern: re.Pattern[str] | None) -> str | None:
    match = pattern.search(text) if pattern is not None else None
    return match.group(0) if match else None


def next_state(spec: SignInSpec, url: str | None, user_code: str | None) -> SignInState:
    """What the user is waited for, once the output has what they need."""
    if url is None or (spec.user_code_pattern is not None and user_code is None):
        return "starting"
    return "waiting_for_code" if spec.paste_code else "waiting_for_browser"


class SignInRun:
    """One sign-in process of one CLI, from its start to its end."""

    def __init__(self, spec: SignInSpec, link_timeout: float, total_timeout: float, tools: CliTools | None) -> None:
        self.spec = spec
        self.tools = tools
        self.link_timeout = link_timeout
        self.total_timeout = total_timeout
        self.state: SignInState = "starting"
        self.url: str | None = None
        self.user_code: str | None = None
        self.code_sent = False
        self.message: str | None = None
        self.process: asyncio.subprocess.Process | None = None
        self.task: asyncio.Task[None] | None = None

    def status(self) -> SignInStatus:
        return SignInStatus(self.state, self.url, self.user_code, self.code_sent, self.message)

    def finish(self, state: SignInState, message: str | None = None) -> None:
        """Only the first ending counts: a cancelled run is not turned into a failed one by the kill."""
        if self.state not in FINISHED_STATES:
            self.state = state
            self.message = message
            logger.info("Sign-in to %s ended: %s", self.spec.key, state)

    async def run(self) -> None:
        try:
            if await self.ensure_installed() and await self.spec.signed_in():
                # Signed in with the CLI of an earlier image: its sign-in folder is still there.
                self.finish("signed_in")
                return
            async with asyncio.timeout(self.total_timeout):
                await self.drive()
        except TimeoutError:
            self.finish("failed", "The sign-in took too long and was stopped. Start again.")
        except SignInFailed as error:
            self.finish("failed", str(error))
        except OSError:
            self.finish("failed", f"{self.spec.display_name} could not be started in this container.")
        finally:
            self.finish("cancelled")
            await self.end_process()

    async def ensure_installed(self) -> bool:
        """Install the CLI first when it is missing or outdated; the time it takes is not part of the sign-in's.
        Whether an install was needed."""
        tool = self.spec.tool
        if self.tools is None or tool is None or self.tools.is_installed(tool):
            return False
        self.state = "installing"
        try:
            await self.tools.ensure(tool)
        except InstallFailed as error:
            raise SignInFailed(str(error)) from None
        if self.state == "installing":
            self.state = "starting"
        return True

    async def drive(self) -> None:
        self.process = await asyncio.create_subprocess_exec(
            *self.spec.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=self.spec.working_directory(),
            env=self.spec.environment(),
            limit=OUTPUT_LINE_LIMIT,
        )
        await self.read_output(self.process)
        code = await self.process.wait()
        if code != 0:
            raise SignInFailed(f"{self.spec.display_name} did not accept the sign-in. Start again.")
        if await self.spec.signed_in():
            self.finish("signed_in")
        else:
            raise SignInFailed(f"The sign-in finished, but {self.spec.display_name} is still not signed in.")

    async def read_output(self, process: asyncio.subprocess.Process) -> None:
        assert process.stdout is not None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.link_timeout
        while True:
            waiting_for_link = self.state == "starting"
            timeout = max(0.0, deadline - loop.time()) if waiting_for_link else None
            try:
                line = await asyncio.wait_for(process.stdout.readline(), timeout)
            except TimeoutError:
                raise SignInFailed(
                    f"{self.spec.display_name} showed no sign-in link in time. Start again, or use the terminal."
                ) from None
            if not line:
                return
            self.take(strip_ansi(line.decode(errors="replace")))

    def take(self, text: str) -> None:
        refused = self.spec.refused_code_pattern
        if self.code_sent and refused is not None and refused.search(text):
            self.code_sent = False
            self.message = "That code was not accepted. Copy the whole code and paste it again."
        if self.url is None:
            link = find_link(text)
            if link is not None:
                if not link_is_allowed(link, self.spec.allowed_hosts):
                    raise SignInFailed("The sign-in link points to an unexpected site, so YAAP did not use it.")
                self.url = link
        if self.user_code is None:
            self.user_code = find_user_code(text, self.spec.user_code_pattern)
        if self.state == "starting":
            self.state = next_state(self.spec, self.url, self.user_code)

    async def send_code(self, raw: str) -> None:
        if self.state != "waiting_for_code" or self.code_sent or self.process is None:
            raise NoSignIn("No sign-in is waiting for a code.")
        code = clean_code(raw)
        stdin = self.process.stdin
        if stdin is None:
            raise NoSignIn("No sign-in is waiting for a code.")
        try:
            stdin.write(f"{code}\n".encode())
            await stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            raise NoSignIn("The sign-in process has already ended.") from None
        self.code_sent = True
        self.message = None

    async def end_process(self) -> None:
        process = self.process
        if process is None:
            return
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
        # Shielded: the run is often ended by a cancelled scope, and the process must be reaped all the same.
        with anyio.CancelScope(shield=True):
            await process.wait()


@dataclass
class CliSignIns:
    """The sign-in runs of all CLIs, at most one per CLI."""

    specs: dict[str, SignInSpec]
    tools: CliTools | None = None
    link_timeout: float = LINK_TIMEOUT_SECONDS
    total_timeout: float = TOTAL_TIMEOUT_SECONDS
    runs: dict[str, SignInRun] = field(default_factory=dict)
    work: BackgroundWork[None] = field(default_factory=BackgroundWork)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @classmethod
    def of(cls, specs: Iterable[SignInSpec], tools: CliTools | None = None) -> "CliSignIns":
        return cls({spec.key: spec for spec in specs}, tools)

    def spec_of(self, key: str) -> SignInSpec:
        try:
            return self.specs[key]
        except KeyError:
            raise UnknownCli(key) from None

    def status(self, key: str) -> SignInStatus:
        self.spec_of(key)
        run = self.runs.get(key)
        return run.status() if run is not None else SignInStatus("idle")

    def refresh_tools(self) -> None:
        """At start: install the CLIs again when an older release installed them."""
        if self.tools is not None:
            self.tools.refresh_if_outdated()

    async def start(self, key: str) -> SignInStatus:
        """Start signing in; a sign-in that is still running for this CLI is cancelled first."""
        spec = self.spec_of(key)
        async with self.lock:
            await self.cancel(key)
            run = SignInRun(spec, self.link_timeout, self.total_timeout, self.tools)
            self.runs[key] = run
            run.task = self.work.start(run.run(), name=f"sign-in-{key}")
            logger.info("Sign-in to %s started", key)
            return run.status()

    async def send_code(self, key: str, code: str) -> SignInStatus:
        self.spec_of(key)
        run = self.runs.get(key)
        if run is None:
            raise NoSignIn("No sign-in is waiting for a code.")
        await run.send_code(code)
        return run.status()

    async def cancel(self, key: str) -> SignInStatus:
        self.spec_of(key)
        run = self.runs.get(key)
        if run is None:
            return SignInStatus("idle")
        run.finish("cancelled")
        if run.task is not None:
            self.work.cancel(run.task)
            await asyncio.gather(run.task, return_exceptions=True)
        return run.status()

    async def stop(self) -> None:
        """At shutdown: end every sign-in process and a running install."""
        await self.work.stop()
        if self.tools is not None:
            await self.tools.stop()
