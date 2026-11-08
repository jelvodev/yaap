"""The preview listener: a second server on YAAP_PREVIEW_PORT, beside the app, that forwards to a dev server.

It is a plain asyncio server rather than a second ASGI app, because a dev server's hot reload needs its WebSocket
relayed as is, and YAAP ships no WebSocket library. Per connection it reads one request head, decides with
app/preview/service.py, opens the dev server (app/preview/upstream.py), sends the rewritten head
(app/preview/http.py) and then relays bytes both ways: the body and the response for HTTP, the frames after a `101`
for a WebSocket. It started in the app's lifespan and stops with it.
"""

import asyncio
import logging
import time

from app.preview.http import (
    HEAD_END,
    MAX_HEAD_BYTES,
    SWITCHING_PROTOCOLS,
    HeadError,
    RequestHead,
    client_response,
    parse_request_head,
    parse_response_head,
    serialize_request,
    serialize_response,
    upstream_request,
)
from app.preview.service import (
    LINK_INVALID,
    NOT_OPENED,
    OPEN_PATH,
    PreviewService,
    Refusal,
    request_refusal,
    session_cookie,
)
from app.preview.upstream import ByteReader, ByteWriter, UpstreamError, dev_server_authority, open_upstream

logger = logging.getLogger(__name__)

HEAD_SECONDS = 30
CHUNK = 65536
STOP_SECONDS = 5
REASONS = {
    303: "See Other",
    400: "Bad Request",
    403: "Forbidden",
    431: "Request Header Fields Too Large",
    502: "Bad Gateway",
}


async def read_head(reader: ByteReader, buffer: bytes = b"") -> tuple[bytes, bytes]:
    """A head up to and including its blank line, and what was read after it; HeadError when it is too long or the
    stream ends first."""
    while HEAD_END not in buffer:
        if len(buffer) > MAX_HEAD_BYTES:
            raise HeadError("The head is too long.")
        chunk = await reader.read(CHUNK)
        if not chunk:
            raise HeadError("The stream ended before the head did.")
        buffer += chunk
    end = buffer.index(HEAD_END) + len(HEAD_END)
    return buffer[:end], buffer[end:]


async def copy(reader: ByteReader, writer: ByteWriter) -> None:
    while chunk := await reader.read(CHUNK):
        writer.write(chunk)
        await writer.drain()


def simple_response(status: int, detail: str, headers: tuple[tuple[str, str], ...] = ()) -> bytes:
    body = detail.encode("utf-8")
    lines = [
        f"HTTP/1.1 {status} {REASONS.get(status, '')}".rstrip(),
        "Content-Type: text/plain; charset=utf-8",
        f"Content-Length: {len(body)}",
        "Cache-Control: no-store",
        "Connection: close",
        *(f"{name}: {value}" for name, value in headers),
    ]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body


async def refuse(writer: ByteWriter, refusal: Refusal) -> None:
    writer.write(simple_response(refusal.status_code, refusal.detail))
    await writer.drain()


async def respond(upstream: ByteReader, writer: ByteWriter) -> None:
    """The dev server's response heads, rewritten (interim 1xx heads included), then everything after as bytes."""
    rest = b""
    while True:
        try:
            data, rest = await read_head(upstream, rest)
            head = parse_response_head(data)
        except HeadError as error:
            raise UpstreamError(f"The dev server's answer cannot be read: {error}") from error
        writer.write(serialize_response(client_response(head)))
        if not (100 <= head.status < 200) or head.status == SWITCHING_PROTOCOLS:
            break
    if rest:
        writer.write(rest)
    await writer.drain()
    await copy(upstream, writer)


async def relay(
    client: tuple[ByteReader, ByteWriter], upstream: tuple[ByteReader, ByteWriter], body: bytes
) -> None:
    """Both directions at once; when either ends (the response is complete, a side closed), the other stops."""
    (client_reader, client_writer), (upstream_reader, upstream_writer) = client, upstream
    if body:
        upstream_writer.write(body)
    await upstream_writer.drain()
    tasks = [
        asyncio.create_task(copy(client_reader, upstream_writer)),
        asyncio.create_task(respond(upstream_reader, client_writer)),
    ]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        for task in tasks:
            task.cancel()


class PreviewListener:
    """The second server. One task per browser connection; all of them end when YAAP stops."""

    def __init__(self, service: PreviewService) -> None:
        self.service = service
        self.server: asyncio.Server | None = None
        self.connections: set[asyncio.Task[None]] = set()

    async def start(self, host: str, port: int) -> int:
        """Listen on host:port (0 picks a free port, for tests); returns the port it listens on."""
        self.server = await asyncio.start_server(self.handle, host, port)
        return int(self.server.sockets[0].getsockname()[1])

    async def stop(self) -> None:
        if self.server is None:
            return
        self.server.close()
        for task in list(self.connections):
            task.cancel()
        try:
            await asyncio.wait_for(self.server.wait_closed(), timeout=STOP_SECONDS)
        except TimeoutError:
            logger.warning("The preview listener did not close within %s seconds.", STOP_SECONDS)
        self.server = None

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self.connections.add(task)
        try:
            await self.serve(reader, writer)
        except (ConnectionError, asyncio.IncompleteReadError, OSError, asyncio.CancelledError):
            # The browser or the dev server went away mid-way; nothing to answer.
            pass
        finally:
            writer.close()
            if task is not None:
                self.connections.discard(task)

    async def serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            data, body = await asyncio.wait_for(read_head(reader), timeout=HEAD_SECONDS)
            head = parse_request_head(data)
        except TimeoutError:
            return
        except HeadError as error:
            await refuse(writer, Refusal(431 if "too long" in str(error) else 400, str(error)))
            return
        refusal = request_refusal(head, self.service.allowed, self.service.config.port)
        if refusal is not None:
            await refuse(writer, refusal)
            return
        if head.path == OPEN_PATH:
            await self.open(head, writer)
            return
        await self.forward(head, body, (reader, writer))

    async def open(self, head: RequestHead, writer: ByteWriter) -> None:
        """Exchange an open token for the preview's cookie, then show the app from its root."""
        value = self.service.open_session(head.query) if head.method == "GET" else None
        if value is None:
            await refuse(writer, LINK_INVALID)
            return
        headers = (("Location", "/"), ("Set-Cookie", session_cookie(value)), ("Referrer-Policy", "no-referrer"))
        writer.write(simple_response(303, "Opening the preview.", headers))
        await writer.drain()

    async def forward(self, head: RequestHead, body: bytes, client: tuple[ByteReader, ByteWriter]) -> None:
        managed = self.service.admitted(head)
        if managed is None or managed.port is None:
            await refuse(client[1], NOT_OPENED)
            return
        # Looking at the preview counts as activity, so the idle stop leaves the dev server running.
        managed.last_touched = time.monotonic()
        try:
            upstream = await open_upstream(managed.target, managed.port)
        except UpstreamError as error:
            await refuse(client[1], Refusal(502, str(error)))
            return
        try:
            upstream[1].write(serialize_request(upstream_request(head, dev_server_authority(managed.port))))
            await relay(client, upstream, body)
        except UpstreamError as error:
            logger.info("Preview of process %s: %s", managed.id, error)
            await refuse(client[1], Refusal(502, str(error)))
        finally:
            upstream[1].close()


async def start_listener(service: PreviewService) -> PreviewListener | None:
    """The preview listener when YAAP_PREVIEW_PORT names a port; YAAP still starts when that port is taken."""
    if not service.enabled:
        return None
    listener = PreviewListener(service)
    try:
        port = await listener.start(service.config.bind, service.config.port)
    except OSError as error:
        logger.error("The preview listener cannot use port %s: %s", service.config.port, error)
        return None
    logger.info("The preview listens on %s:%s.", service.config.bind, port)
    return listener
