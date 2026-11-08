"""Running MCP servers.

A server starts when a run first needs its tools, stays running while it is used and stops after IDLE_SECONDS
without use. Runs with the same configuration (and the same secret values) share it. A server that stopped is
started again on its next use. Its tool list is read when it starts and on refresh.
"""

import asyncio
import contextlib
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from app.mcp_client.client import McpError, McpTimeout, Session, open_session
from app.mcp_client.config import ServerConfig
from app.mcp_client.remote import PlaceFactory, RemoteStdioSession

logger = logging.getLogger(__name__)

IDLE_SECONDS = 600
MAX_TOOL_NAME = 64
UNSAFE = re.compile(r"[^A-Za-z0-9_-]")
MAX_RESULT_CHARS = 200_000


@dataclass(frozen=True)
class McpTool:
    # The name the model sees: <server>__<tool>.
    name: str
    server: str
    tool: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool
    auto: bool
    key: str


@dataclass(frozen=True)
class McpResult:
    text: str
    images: tuple[tuple[str, str], ...]
    is_error: bool


def tool_name(server: str, tool: str) -> str:
    return f"{server}__{UNSAFE.sub('_', tool)}"[:MAX_TOOL_NAME]


def connection_key(server: ServerConfig, scope: str = "") -> str:
    return f"{server.name}:{server.fingerprint()}:{scope}"


def result_of(result: dict[str, Any]) -> McpResult:
    texts: list[str] = []
    images: list[tuple[str, str]] = []
    for item in result.get("content") or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "text":
            texts.append(str(item.get("text") or ""))
        elif kind == "image" and item.get("data"):
            images.append((str(item.get("mimeType") or "image/png"), str(item["data"])))
        elif kind == "resource":
            resource = item.get("resource") or {}
            texts.append(str(resource.get("text") or f"[Resource {resource.get('uri', '')}]"))
        elif kind == "resource_link":
            texts.append(f"[Resource {item.get('uri', '')}]")
        elif kind == "audio":
            texts.append("[Audio left out]")
    if not texts and result.get("structuredContent") is not None:
        texts.append(json.dumps(result["structuredContent"], indent=2, ensure_ascii=False))
    text = "\n".join(texts)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + f"\n[Cut at {MAX_RESULT_CHARS:,} characters.]"
    return McpResult(text, tuple(images), bool(result.get("isError")))


class Connection:
    def __init__(self, key: str, server: ServerConfig, cwd: str | None, place: PlaceFactory | None = None) -> None:
        self.key = key
        self.server = server
        self.cwd = cwd
        # Where a stdio server runs: in the environment's runner over SSH; None is YAAP's own process.
        self.place = place
        self.session: Session | None = None
        self.raw_tools: list[dict[str, Any]] = []
        self.error: str | None = None
        self.last_used = time.monotonic()
        self.started_at: float | None = None
        self.starts = 0
        self.lock = asyncio.Lock()
        self.calls = 0
        # Its own idle time (a conversation's browser); None is the manager's.
        self.idle_seconds: float | None = None

    @property
    def running(self) -> bool:
        return self.session is not None and self.session.alive

    def log(self) -> list[str]:
        return list(self.session.log) if self.session else []

    async def ensure(self) -> None:
        async with self.lock:
            if self.running:
                return
            if self.session is not None:
                await self.session.close()
            if self.place is not None and self.server.transport == "stdio":
                session: Session = RemoteStdioSession(self.server, self.place, self.cwd)
            else:
                session = open_session(self.server, self.cwd)
            self.session = session
            self.starts += 1
            try:
                await session.open()
                await session.initialize()
                self.raw_tools = await session.list_tools()
            except McpError as error:
                self.error = str(error)
                tail = "\n".join(list(session.log)[-5:])
                await session.close()
                raise McpError(f"{error}{' Server output: ' + tail if tail else ''}") from error
            self.error = None
            self.started_at = time.monotonic()

    async def refresh(self) -> None:
        await self.ensure()
        assert self.session is not None
        self.raw_tools = await self.session.list_tools()

    def tools(self) -> list[McpTool]:
        offered = []
        for raw in self.raw_tools:
            name = raw["name"]
            if name in self.server.disabled_tools:
                continue
            if self.server.only_tools is not None and name not in self.server.only_tools:
                continue
            schema = raw.get("inputSchema") if isinstance(raw.get("inputSchema"), dict) else {"type": "object", "properties": {}}
            annotations = raw.get("annotations") if isinstance(raw.get("annotations"), dict) else {}
            description = str(raw.get("description") or raw.get("title") or name)
            offered.append(
                McpTool(
                    name=tool_name(self.server.name, name),
                    server=self.server.name,
                    tool=name,
                    description=f"[MCP server {self.server.name}] {description}"[:4000],
                    input_schema=schema,
                    read_only=bool(annotations.get("readOnlyHint")),
                    auto=name in self.server.auto_tools,
                    key=self.key,
                )
            )
        return offered

    async def call(self, tool: str, arguments: dict[str, Any], timeout: float | None) -> McpResult:
        await self.ensure()
        assert self.session is not None
        self.last_used = time.monotonic()
        self.calls += 1
        try:
            return result_of(await self.session.call_tool(tool, arguments, timeout))
        except McpTimeout:
            # A server that does not answer may be stuck: the next call starts it again.
            await self.stop()
            raise
        finally:
            self.last_used = time.monotonic()

    async def stop(self) -> None:
        async with self.lock:
            if self.session is not None:
                await self.session.close()
            self.started_at = None


class McpManager:
    def __init__(self, idle_seconds: float = IDLE_SECONDS) -> None:
        self.connections: dict[str, Connection] = {}
        self.idle_seconds = idle_seconds

    def connection(
        self, server: ServerConfig, scope: str = "", cwd: str | None = None, place: PlaceFactory | None = None
    ) -> Connection:
        key = connection_key(server, scope)
        found = self.connections.get(key)
        if found is None:
            found = Connection(key, server, cwd, place)
            self.connections[key] = found
        return found

    async def tools(
        self, servers: list[ServerConfig], scope: str = "", cwd: str | None = None, place: PlaceFactory | None = None
    ) -> tuple[list[McpTool], dict[str, str]]:
        """The tools of these servers, starting them when needed; and per server that failed, why."""
        tools: list[McpTool] = []
        errors: dict[str, str] = {}

        async def load(server: ServerConfig) -> None:
            connection = self.connection(server, scope, cwd, place)
            try:
                await connection.ensure()
            except McpError as error:
                errors[server.name] = str(error)
                return
            connection.last_used = time.monotonic()
            tools.extend(connection.tools())

        await asyncio.gather(*(load(server) for server in servers))
        seen: set[str] = set()
        unique = []
        for tool in sorted(tools, key=lambda item: item.name):
            if tool.name not in seen:
                seen.add(tool.name)
                unique.append(tool)
        return unique, errors

    async def call(self, tool: McpTool, arguments: dict[str, Any], timeout: float | None = None) -> McpResult:
        connection = self.connections.get(tool.key)
        if connection is None:
            raise McpError(f"The MCP server {tool.server} is no longer configured.")
        return await connection.call(tool.tool, arguments, timeout)

    async def stop_idle(self) -> int:
        now = time.monotonic()
        stopped = 0
        for key, connection in list(self.connections.items()):
            idle = connection.idle_seconds or self.idle_seconds
            if connection.running and now - connection.last_used > idle:
                await connection.stop()
                stopped += 1
            if not connection.running and now - connection.last_used > idle * 6:
                self.connections.pop(key, None)
        return stopped

    async def stop_scope(self, scope: str) -> None:
        """Stop the servers of one scope (a conversation's browser)."""
        for key, connection in list(self.connections.items()):
            if key.endswith(f":{scope}") and scope:
                await connection.stop()
                self.connections.pop(key, None)

    async def stop_idle_periodically(self, interval: float = 60) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await self.stop_idle()
            except Exception:
                logger.exception("Stopping idle MCP servers failed.")

    async def restart(
        self, server: ServerConfig, scope: str = "", cwd: str | None = None, place: PlaceFactory | None = None
    ) -> Connection:
        connection = self.connection(server, scope, cwd, place)
        await connection.stop()
        await connection.ensure()
        return connection

    async def shutdown(self) -> None:
        for connection in list(self.connections.values()):
            with contextlib.suppress(Exception):
                await connection.stop()
        self.connections.clear()

    def find(self, server: ServerConfig, scope: str = "") -> Connection | None:
        return self.connections.get(connection_key(server, scope))
