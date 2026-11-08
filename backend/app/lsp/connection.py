"""JSON-RPC 2.0 with the Language Server Protocol's framing: a `Content-Length` header, an empty line, the body.

`JsonRpcConnection` reads messages from a byte stream in a task of its own: answers resolve the request that waits for
them, notifications go to a callback, and requests from the server are answered by another callback. The streams are
asyncio's (a local process) or asyncssh's (a process in the runner); both offer `readline` and `readexactly`.
"""

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# A body larger than this is refused: no answer YAAP asks for comes near it, and it would be held in memory.
MAX_BODY_BYTES = 64 * 1024 * 1024
METHOD_NOT_FOUND = -32601


class LspError(Exception):
    """The server could not be started or reached, did not answer in time, or answered with an error."""


class LspTimeout(LspError):
    """No answer in time; the server may be busy or stuck."""


class LspUnavailable(LspError):
    """The server's program is not installed where it should run; trying again soon will not help."""


class MethodNotFound(Exception):
    """A request from the server that YAAP does not handle."""


class ByteReader(Protocol):
    async def readline(self) -> bytes: ...

    async def readexactly(self, n: int) -> bytes: ...


class ByteWriter(Protocol):
    def write(self, data: bytes) -> None: ...

    async def drain(self) -> None: ...


# (method, params) -> None for a notification; (method, params) -> result for a request (raises MethodNotFound).
NotificationHandler = Callable[[str, Any], None]
RequestHandler = Callable[[str, Any], Any]


def encode_message(message: dict[str, Any]) -> bytes:
    body = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


async def read_message(reader: ByteReader) -> dict[str, Any] | None:
    """The next message, or None at the end of the stream. Raises LspError on a broken frame."""
    length: int | None = None
    while True:
        line = await reader.readline()
        if not line:
            return None
        text = line.decode("ascii", errors="replace").strip()
        if not text:
            # Tolerate stray empty lines before the headers; the empty line after them ends the headers.
            if length is not None:
                break
            continue
        name, _, value = text.partition(":")
        if name.strip().lower() == "content-length":
            try:
                length = int(value.strip())
            except ValueError as error:
                raise LspError(f"The server sent an invalid Content-Length: {value.strip()}") from error
    if length < 0 or length > MAX_BODY_BYTES:
        raise LspError(f"The server sent a message of {length} bytes, which YAAP does not read.")
    body = await reader.readexactly(length)
    try:
        message = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LspError(f"The server sent a message that is not JSON: {error}") from error
    if not isinstance(message, dict):
        raise LspError("The server sent a message that is not a JSON object.")
    return message


class JsonRpcConnection:
    def __init__(
        self,
        reader: ByteReader,
        writer: ByteWriter,
        on_notification: NotificationHandler,
        on_request: RequestHandler,
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.on_notification = on_notification
        self.on_request = on_request
        self.pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.next_id = 0
        self.closed = False
        self.close_reason = ""
        self.task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self.task = asyncio.create_task(self.read_loop())

    async def read_loop(self) -> None:
        reason = "The server stopped."
        try:
            while True:
                message = await read_message(self.reader)
                if message is None:
                    break
                await self.dispatch(message)
        except asyncio.IncompleteReadError:
            reason = "The server stopped in the middle of a message."
        except LspError as error:
            reason = str(error)
        except (OSError, ConnectionError) as error:
            reason = f"The connection to the server broke off: {error}"
        finally:
            self.mark_closed(reason)

    def mark_closed(self, reason: str) -> None:
        if not self.closed:
            self.closed, self.close_reason = True, reason
        for future in self.pending.values():
            if not future.done():
                future.set_exception(LspError(self.close_reason))
        self.pending.clear()

    async def dispatch(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        if isinstance(method, str) and "id" in message:
            await self.answer(message["id"], method, message.get("params"))
        elif isinstance(method, str):
            try:
                self.on_notification(method, message.get("params"))
            except (KeyError, TypeError, ValueError) as error:
                logger.warning("A %s notification from a language server could not be read: %s", method, error)
        elif "id" in message:
            future = self.pending.pop(message["id"], None) if isinstance(message["id"], int) else None
            if future is not None and not future.done():
                future.set_result(message)

    async def answer(self, request_id: Any, method: str, params: Any) -> None:
        try:
            result = self.on_request(method, params)
            reply: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "result": result}
        except MethodNotFound:
            reply = {"jsonrpc": "2.0", "id": request_id, "error": {"code": METHOD_NOT_FOUND, "message": "Not supported"}}
        with contextlib.suppress(LspError):
            await self.send(reply)

    async def send(self, message: dict[str, Any]) -> None:
        if self.closed:
            raise LspError(self.close_reason or "The server is not running.")
        try:
            # One write per frame, so frames of concurrent senders never interleave.
            self.writer.write(encode_message(message))
            await self.writer.drain()
        except (OSError, ConnectionError, RuntimeError) as error:
            self.mark_closed("The server stopped.")
            raise LspError("The server stopped.") from error

    async def request(self, method: str, params: Any, timeout: float) -> Any:
        """Send a request and wait for its answer; its result, or LspError with the server's message."""
        if self.closed:
            raise LspError(self.close_reason or "The server is not running.")
        self.next_id += 1
        request_id = self.next_id
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            response = await asyncio.wait_for(future, timeout)
        except TimeoutError as error:
            with contextlib.suppress(LspError):
                await self.notify("$/cancelRequest", {"id": request_id})
            raise LspTimeout(f"{method} got no answer within {timeout:g} seconds.") from error
        finally:
            self.pending.pop(request_id, None)
        if "error" in response:
            error = response.get("error") or {}
            raise LspError(f"{method} failed: {error.get('message') if isinstance(error, dict) else error}")
        return response.get("result")

    async def notify(self, method: str, params: Any) -> None:
        await self.send({"jsonrpc": "2.0", "method": method, "params": params})

    async def stop_reading(self) -> None:
        if self.task is not None and not self.task.done():
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        self.mark_closed("The server was stopped.")
