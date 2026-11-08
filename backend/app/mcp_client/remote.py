"""MCP servers over stdio in a runner: the server's standard input and output go over SSH.

A server's command does not run in YAAP's container but in the environment's runner (app/runner), so it can read
nothing of YAAP. YAAP opens an SSH channel with a fixed launcher; the first line it sends on that channel is a script,
base64 so it is one line, that sets the server's environment (its vault values, filled in per start), changes to its
folder and `exec`s the server. Every following line is the MCP conversation itself. bash reads that first line byte
by byte from a pipe, so nothing of the conversation is taken from the server. Values never appear on a command line.
"""

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import asyncssh

from app.mcp_client.client import MAX_LINE_BYTES, McpError, Session
from app.mcp_client.config import ServerConfig
from app.targets.base import TargetError
from app.targets.stdio import LAUNCHER, exec_line, exec_script

__all__ = ["LAUNCHER", "PlaceFactory", "RemotePlace", "RemoteStdioSession", "launch_line", "launch_script"]


class RemotePlace(Protocol):
    """Where a server runs: a runner (or any SSH target) that hands out its connection."""

    async def connection(self) -> asyncssh.SSHClientConnection: ...

    def note_use(self) -> None: ...

    def is_open(self) -> bool: ...


PlaceFactory = Callable[[], Awaitable[RemotePlace]]


def launch_script(server: ServerConfig, cwd: str | None) -> str:
    """The script the launcher runs: the server's variables, its folder, then the server in place of the shell."""
    try:
        return exec_script((server.command, *server.args), server.env, cwd)
    except TargetError as error:
        raise McpError(str(error)) from error


def launch_line(server: ServerConfig, cwd: str | None) -> bytes:
    return exec_line(launch_script(server, cwd))


class RemoteStdioSession(Session):
    def __init__(self, server: ServerConfig, place: PlaceFactory, cwd: str | None = None) -> None:
        super().__init__(server)
        self.place_factory = place
        self.place: RemotePlace | None = None
        self.cwd = cwd
        self.process: asyncssh.SSHClientProcess[bytes] | None = None
        self.tasks: list[asyncio.Task[None]] = []

    async def open(self) -> None:
        try:
            self.place = await self.place_factory()
            connection = await self.place.connection()
            self.process = await connection.create_process(LAUNCHER, encoding=None)
        except (TargetError, asyncssh.Error, OSError) as error:
            raise McpError(f"Could not start {self.server.command} in the runner: {error}") from error
        self.process.stdin.write(launch_line(self.server, self.cwd))
        self.tasks = [asyncio.create_task(self.read_stdout()), asyncio.create_task(self.read_stderr())]

    async def read_stdout(self) -> None:
        assert self.process is not None
        try:
            while True:
                line = await self.process.stdout.readline()
                if not line:
                    break
                if len(line) > MAX_LINE_BYTES:
                    self.note("YAAP: a line from the server was too long and was left out.")
                    continue
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    message = json.loads(text)
                except json.JSONDecodeError:
                    self.note(f"stdout: {text}")
                    continue
                await self.handle(message)
        except (asyncssh.Error, OSError) as error:
            self.note(f"YAAP: the connection to the runner broke off: {error}")
        finally:
            self.closed = True
            self.fail_pending("The server stopped.")

    async def read_stderr(self) -> None:
        assert self.process is not None
        with contextlib.suppress(asyncssh.Error, OSError):
            while True:
                line = await self.process.stderr.readline()
                if not line:
                    break
                self.note(line.decode("utf-8", errors="replace"))

    async def send(self, message: dict[str, Any]) -> None:
        if self.process is None or not self.alive:
            raise McpError("The server is not running.")
        if self.place is not None:
            self.place.note_use()
        try:
            self.process.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
            await self.process.stdin.drain()
        except (asyncssh.Error, OSError, BrokenPipeError) as error:
            raise McpError("The server stopped.") from error

    @property
    def alive(self) -> bool:
        return not self.closed and self.process is not None and self.process.exit_status is None

    async def close(self) -> None:
        await super().close()
        process = self.process
        if process is not None:
            with contextlib.suppress(asyncssh.Error, OSError):
                process.stdin.write_eof()
            try:
                await asyncio.wait_for(process.wait(check=False), 2)
            except (TimeoutError, asyncssh.Error, OSError):
                # Closing the channel ends the server and what it started.
                process.close()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
