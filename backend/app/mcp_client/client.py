"""A small MCP client: JSON-RPC 2.0 over stdio, Streamable HTTP or the older HTTP+SSE transport.

Only what YAAP needs: initialize, tools/list and tools/call. Requests the server sends to the client are answered
with "method not found", except ping.
"""

import asyncio
import collections
import contextlib
import json
import logging
import sys
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urljoin

import httpx

from app.core.subprocess_env import safe_subprocess_env
from app.mcp_client.config import ServerConfig
from app.version import VERSION

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "YAAP", "version": VERSION}
LOG_LINES = 200
MAX_LINE_BYTES = 16 * 1024 * 1024


class McpError(Exception):
    """The server could not be reached, did not answer in time, or answered with an error."""


class McpTimeout(McpError):
    """No answer in time; the server may be stuck."""


class Session:
    """One connection to a server. Subclasses send messages; responses resolve the pending futures."""

    def __init__(self, server: ServerConfig) -> None:
        self.server = server
        self.pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.next_id = 0
        self.log: collections.deque[str] = collections.deque(maxlen=LOG_LINES)
        self.closed = False

    def note(self, line: str) -> None:
        self.log.append(line.rstrip()[:2000])

    async def send(self, message: dict[str, Any]) -> None:
        raise NotImplementedError

    async def open(self) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        self.closed = True
        self.fail_pending("The server connection closed.")

    def fail_pending(self, reason: str) -> None:
        for future in self.pending.values():
            if not future.done():
                future.set_exception(McpError(reason))
        self.pending.clear()

    @property
    def alive(self) -> bool:
        return not self.closed

    async def handle(self, message: Any) -> None:
        if isinstance(message, list):
            for item in message:
                await self.handle(item)
            return
        if not isinstance(message, dict):
            return
        if "method" in message and "id" in message:
            # A request from the server: YAAP offers no sampling, roots or elicitation.
            if message["method"] == "ping":
                await self.send({"jsonrpc": "2.0", "id": message["id"], "result": {}})
            else:
                await self.send({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "Not supported"}})
            return
        future = self.pending.pop(message.get("id"), None) if "id" in message else None
        if future is not None and not future.done():
            future.set_result(message)

    async def request(self, method: str, params: dict[str, Any] | None, timeout: float) -> dict[str, Any]:
        if self.closed:
            raise McpError("The server is not running.")
        self.next_id += 1
        request_id = self.next_id
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        try:
            await self.send(message)
            response = await asyncio.wait_for(future, timeout)
        except TimeoutError as error:
            raise McpTimeout(f"{method} got no answer within {timeout:.0f} seconds.") from error
        finally:
            self.pending.pop(request_id, None)
        if "error" in response:
            error = response["error"] or {}
            raise McpError(f"{method} failed: {error.get('message') or error}")
        return response.get("result") or {}

    async def notify(self, method: str) -> None:
        await self.send({"jsonrpc": "2.0", "method": method})

    async def initialize(self) -> dict[str, Any]:
        result = await self.request(
            "initialize",
            {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": CLIENT_INFO},
            self.server.start_timeout,
        )
        await self.notify("notifications/initialized")
        return result

    async def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor = None
        for _ in range(20):
            result = await self.request("tools/list", {"cursor": cursor} if cursor else {}, self.server.start_timeout)
            tools += [tool for tool in result.get("tools", []) if isinstance(tool, dict) and isinstance(tool.get("name"), str)]
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        return await self.request("tools/call", {"name": name, "arguments": arguments}, timeout or self.server.call_timeout)


class StdioSession(Session):
    def __init__(self, server: ServerConfig, cwd: str | None = None) -> None:
        super().__init__(server)
        self.cwd = cwd
        self.process: asyncio.subprocess.Process | None = None
        self.tasks: list[asyncio.Task[None]] = []

    async def open(self) -> None:
        # The server's configured variables (vault values filled in) on top of the safe environment.
        environment = safe_subprocess_env(dict(self.server.env))
        options: dict[str, Any] = {}
        if sys.platform != "win32":
            options["start_new_session"] = True
        try:
            self.process = await asyncio.create_subprocess_exec(
                self.server.command,
                *self.server.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=environment,
                cwd=self.cwd,
                limit=MAX_LINE_BYTES,
                **options,
            )
        except OSError as error:
            raise McpError(f"Could not start {self.server.command}: {error}") from error
        self.tasks = [asyncio.create_task(self.read_stdout()), asyncio.create_task(self.read_stderr())]

    async def read_stdout(self) -> None:
        assert self.process and self.process.stdout
        try:
            while True:
                line = await self.process.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    message = json.loads(text)
                except json.JSONDecodeError:
                    self.note(f"stdout: {text}")
                    continue
                await self.handle(message)
        except (ValueError, asyncio.LimitOverrunError) as error:
            self.note(f"YAAP: unreadable output from the server: {error}")
        finally:
            self.closed = True
            self.fail_pending("The server stopped.")

    async def read_stderr(self) -> None:
        assert self.process and self.process.stderr
        while True:
            line = await self.process.stderr.readline()
            if not line:
                break
            self.note(line.decode("utf-8", errors="replace"))

    async def send(self, message: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None or self.process.returncode is not None:
            raise McpError("The server is not running.")
        try:
            self.process.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
            await self.process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as error:
            raise McpError("The server stopped.") from error

    @property
    def alive(self) -> bool:
        return not self.closed and self.process is not None and self.process.returncode is None

    async def close(self) -> None:
        await super().close()
        process = self.process
        if process is not None and process.returncode is None:
            with contextlib.suppress(Exception):
                if process.stdin:
                    process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), 2)
            except TimeoutError:
                stop_process_tree(process)
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(process.wait(), 5)
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)


def stop_process_tree(process: asyncio.subprocess.Process) -> None:
    from app.targets.container import stop_tree

    stop_tree(process)


async def sse_events(response: httpx.Response) -> AsyncIterator[tuple[str, str]]:
    """(event, data) pairs of a text/event-stream response."""
    event, data = "message", []
    async for line in response.aiter_lines():
        if line == "":
            if data:
                yield event, "\n".join(data)
            event, data = "message", []
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        yield event, "\n".join(data)


class HttpSession(Session):
    """Streamable HTTP: each message is a POST; the answer is JSON or an event stream."""

    def __init__(self, server: ServerConfig) -> None:
        super().__init__(server)
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(server.call_timeout, connect=server.start_timeout))
        self.session_id: str | None = None
        self.streams: set[asyncio.Task[None]] = set()

    async def open(self) -> None:
        return None

    def headers(self) -> dict[str, str]:
        headers = {**dict(self.server.headers), "Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        headers["MCP-Protocol-Version"] = PROTOCOL_VERSION
        return headers

    async def send(self, message: dict[str, Any]) -> None:
        try:
            request = self.client.build_request("POST", self.server.url, json=message, headers=self.headers())
            response = await self.client.send(request, stream=True)
        except httpx.HTTPError as error:
            raise McpError(f"Could not reach {self.server.url}: {error}") from error
        session_id = response.headers.get("mcp-session-id")
        if session_id:
            self.session_id = session_id
        if response.status_code >= 400:
            body = (await response.aread()).decode(errors="replace")[:500]
            await response.aclose()
            raise McpError(f"The server answered HTTP {response.status_code}: {body}")
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            task = asyncio.create_task(self.read_stream(response))
            self.streams.add(task)
            task.add_done_callback(self.streams.discard)
            return
        body = await response.aread()
        await response.aclose()
        if body.strip():
            try:
                await self.handle(json.loads(body))
            except json.JSONDecodeError:
                self.note(f"Unreadable answer: {body[:300]!r}")

    async def read_stream(self, response: httpx.Response) -> None:
        try:
            async for _event, data in sse_events(response):
                try:
                    await self.handle(json.loads(data))
                except json.JSONDecodeError:
                    self.note(f"Unreadable event: {data[:300]}")
        except httpx.HTTPError as error:
            self.note(f"The event stream broke off: {error}")
        finally:
            await response.aclose()

    async def close(self) -> None:
        await super().close()
        for task in list(self.streams):
            task.cancel()
        if self.session_id:
            with contextlib.suppress(Exception):
                await self.client.delete(self.server.url, headers=self.headers(), timeout=3)
        await self.client.aclose()


class SseSession(Session):
    """The older HTTP+SSE transport: a GET event stream names the endpoint for POSTs; answers come on the stream."""

    def __init__(self, server: ServerConfig) -> None:
        super().__init__(server)
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=server.start_timeout))
        self.endpoint: str | None = None
        self.ready = asyncio.Event()
        self.reader: asyncio.Task[None] | None = None

    async def open(self) -> None:
        self.reader = asyncio.create_task(self.read())
        try:
            await asyncio.wait_for(self.ready.wait(), self.server.start_timeout)
        except TimeoutError as error:
            await self.close()
            raise McpError("The server sent no endpoint in time.") from error
        if self.endpoint is None:
            raise McpError("The server's event stream closed.")

    async def read(self) -> None:
        try:
            async with self.client.stream("GET", self.server.url, headers={**dict(self.server.headers), "Accept": "text/event-stream"}) as response:
                if response.status_code >= 400:
                    self.note(f"The server answered HTTP {response.status_code}.")
                    return
                async for event, data in sse_events(response):
                    if event == "endpoint":
                        self.endpoint = urljoin(self.server.url, data.strip())
                        self.ready.set()
                    else:
                        try:
                            await self.handle(json.loads(data))
                        except json.JSONDecodeError:
                            self.note(f"Unreadable event: {data[:300]}")
        except httpx.HTTPError as error:
            self.note(f"The event stream broke off: {error}")
        finally:
            self.closed = True
            self.ready.set()
            self.fail_pending("The server's event stream closed.")

    async def send(self, message: dict[str, Any]) -> None:
        if self.endpoint is None:
            raise McpError("The server is not connected.")
        try:
            response = await self.client.post(
                self.endpoint, json=message, headers={**dict(self.server.headers), "Content-Type": "application/json"}, timeout=30
            )
        except httpx.HTTPError as error:
            raise McpError(f"Could not reach the server: {error}") from error
        if response.status_code >= 400:
            raise McpError(f"The server answered HTTP {response.status_code}.")

    async def close(self) -> None:
        await super().close()
        if self.reader is not None:
            self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)
        await self.client.aclose()


def open_session(server: ServerConfig, cwd: str | None = None) -> Session:
    if server.transport == "stdio":
        return StdioSession(server, cwd)
    if server.transport == "sse":
        return SseSession(server)
    return HttpSession(server)
