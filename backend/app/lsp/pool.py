"""The running language servers of this YAAP: one per place, project folder and language.

A server starts when a tool first needs it, is started again when it stopped (at most MAX_RESTARTS times in
RESTART_WINDOW, so a server that keeps crashing is not started over and over), and stops after the idle time of the
runner and sandboxes (an hour), like the container it runs in. A program that is not installed where the server should
run is remembered for a while, so edits do not try it again and again.
"""

import asyncio
import collections
import contextlib
import logging
import time
from dataclasses import dataclass, field

from app.lsp.client import LanguageClient
from app.lsp.connection import LspError, LspUnavailable
from app.lsp.process import NOT_FOUND, Place
from app.lsp.servers import SERVERS, ServerSpec
from app.sandbox.service import SANDBOX_IDLE

logger = logging.getLogger(__name__)

MAX_RESTARTS = 3
RESTART_WINDOW = 300.0
MISSING_SECONDS = 600.0
IDLE_SECONDS = SANDBOX_IDLE.total_seconds()
ServerKey = tuple[str, str, str]


def missing_message(spec: ServerSpec) -> str:
    where = (
        "It comes with the sandbox image: update YAAP to get a newer image."
        if spec.in_image
        else "Install it through the sandbox image's extra layer (docs/configuration.md, \"Extra packages for the "
        "runner\")."
    )
    return f"{spec.program} ({spec.name}) is not installed where this project's commands run. {where}"


@dataclass
class Managed:
    client: LanguageClient | None = None
    last_used: float = field(default_factory=time.monotonic)
    # When the server was found stopped while it was wanted, within the restart window.
    crashes: collections.deque[float] = field(default_factory=collections.deque)
    last_failure: str = ""


class LanguageServerPool:
    def __init__(self, idle_seconds: float = IDLE_SECONDS) -> None:
        self.idle_seconds = idle_seconds
        self.servers: dict[ServerKey, Managed] = {}
        # (place, program) -> when it was found missing there.
        self.missing: dict[tuple[str, str], float] = {}
        # The programs outside the image found in a runner or sandbox, per place; asked once per place.
        self.found: dict[str, set[str]] = {}
        self.locks: dict[tuple[ServerKey, int], asyncio.Lock] = {}

    @staticmethod
    def key(place: Place, spec: ServerSpec) -> ServerKey:
        return (place.key, place.root, spec.language)

    def lock(self, key: ServerKey) -> asyncio.Lock:
        # One lock per server and event loop (the tests run several loops one after another).
        return self.locks.setdefault((key, id(asyncio.get_running_loop())), asyncio.Lock())

    def is_missing(self, place: Place, spec: ServerSpec) -> bool:
        when = self.missing.get((place.key, spec.program))
        return when is not None and time.monotonic() - when < MISSING_SECONDS

    def can_run(self, place: Place, spec: ServerSpec, installed_here: bool) -> bool:
        """Whether a server for this language can run there: its program is installed (for a local place the caller
        looked it up; in a runner or sandbox the image has it or the probe found it) and was not found missing."""
        if self.is_missing(place, spec):
            return False
        if place.remote is None:
            return installed_here
        return spec.in_image or spec.program in self.found.get(place.key, set())

    def running(self, place: Place, spec: ServerSpec) -> LanguageClient | None:
        """The server when it runs now in this event loop; never starts one."""
        managed = self.servers.get(self.key(place, spec))
        if managed is None or managed.client is None:
            return None
        client = managed.client
        if not client.alive or client.loop is not asyncio.get_running_loop():
            return None
        managed.last_used = time.monotonic()
        place.note_use()
        return client

    async def get(self, place: Place, spec: ServerSpec) -> LanguageClient:
        """The running server, started (again) when needed. Raises LspUnavailable or LspError with what went wrong."""
        key = self.key(place, spec)
        async with self.lock(key):
            managed = self.servers.setdefault(key, Managed())
            client = self.usable(managed)
            if client is not None:
                managed.last_used = time.monotonic()
                place.note_use()
                return client
            self.check_restarts(managed, spec)
            if self.is_missing(place, spec):
                raise LspUnavailable(missing_message(spec))
            client = await self.start(place, spec)
            managed.client, managed.last_used = client, time.monotonic()
        if place.remote is not None and place.key not in self.found:
            self.found[place.key] = await place.probe([item.program for item in SERVERS if not item.in_image])
        return client

    def usable(self, managed: Managed) -> LanguageClient | None:
        """The managed server when it can be used now; a stopped one counts as a crash, one of an old loop does not."""
        client = managed.client
        if client is None:
            return None
        if client.loop is not asyncio.get_running_loop():
            client.kill()
            managed.client = None
            return None
        if client.alive:
            return client
        managed.crashes.append(time.monotonic())
        managed.last_failure = client.failure()
        client.kill()
        managed.client = None
        return None

    @staticmethod
    def check_restarts(managed: Managed, spec: ServerSpec) -> None:
        now = time.monotonic()
        while managed.crashes and now - managed.crashes[0] > RESTART_WINDOW:
            managed.crashes.popleft()
        if len(managed.crashes) > MAX_RESTARTS:
            raise LspError(
                f"The {spec.name} language server stopped {len(managed.crashes)} times in the last "
                f"{RESTART_WINDOW / 60:g} minutes, so YAAP does not start it again for now. Its last output: "
                f"{managed.last_failure}"
            )

    async def start(self, place: Place, spec: ServerSpec) -> LanguageClient:
        try:
            process = await place.spawn(spec.command)
        except LspUnavailable as error:
            self.missing[(place.key, spec.program)] = time.monotonic()
            raise LspUnavailable(missing_message(spec)) from error
        client = LanguageClient(spec, process, place.paths, local=place.remote is None)
        try:
            await client.start()
        except LspError as error:
            await process.wait(1)
            client.kill()
            if place.remote is not None and process.exit_code == NOT_FOUND:
                self.missing[(place.key, spec.program)] = time.monotonic()
                raise LspUnavailable(missing_message(spec)) from error
            raise LspError(f"{spec.name} did not start: {error} {client.failure()}".strip()) from error
        return client

    async def stop_idle(self) -> int:
        """Stop the servers unused for the idle time; returns how many stopped."""
        now = time.monotonic()
        stopped = 0
        for key, managed in list(self.servers.items()):
            if now - managed.last_used <= self.idle_seconds:
                continue
            if managed.client is not None:
                await self.stop_client(managed.client)
                stopped += 1
            self.servers.pop(key, None)
        return stopped

    async def stop_all(self) -> None:
        for managed in list(self.servers.values()):
            if managed.client is not None:
                await self.stop_client(managed.client)
        self.servers.clear()
        self.locks.clear()

    @staticmethod
    async def stop_client(client: LanguageClient) -> None:
        if client.loop is asyncio.get_running_loop():
            with contextlib.suppress(LspError, OSError):
                await client.stop()
        else:
            client.kill()

    async def stop_idle_periodically(self, interval: float = 300) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await self.stop_idle()
            except Exception:  # noqa: BLE001 - the pass must keep running
                logger.exception("Stopping idle language servers failed.")


LANGUAGE_SERVERS = LanguageServerPool()
