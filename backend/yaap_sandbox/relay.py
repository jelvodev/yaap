"""The SSH relay: YAAP reaches a sandbox's sshd through the helper, never on a network it shares with sandboxes.

YAAP opens a TCP connection to port 2222 and sends one line, `YAAP-SANDBOX <token> <key>`. The helper checks the
token and that the key names a running sandbox with YAAP's label, answers `OK` and from then on copies bytes in both
directions; otherwise it answers `ERR <reason>` and closes. SSH itself runs end to end between YAAP and the sandbox,
with the host key YAAP pinned, so the helper sees only encrypted traffic.
"""

import asyncio
import hmac
from collections.abc import Awaitable, Callable

from yaap_sandbox.sandboxes import KEY, SandboxError, Sandboxes

GREETING = "YAAP-SANDBOX"
LINE_LIMIT = 512
GREETING_SECONDS = 10
CONNECT_SECONDS = 10

Address = Callable[[str], Awaitable[tuple[str, int]]]


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while chunk := await reader.read(65536):
            writer.write(chunk)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        try:
            writer.close()
        except (ConnectionError, OSError, RuntimeError):
            pass


def relay_handler(token: str, address: Address) -> Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        async def refuse(reason: str) -> None:
            writer.write(f"ERR {reason}\n".encode())
            try:
                await writer.drain()
            finally:
                writer.close()

        try:
            line = await asyncio.wait_for(reader.readuntil(b"\n"), GREETING_SECONDS)
        except (TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
            writer.close()
            return
        parts = line[:LINE_LIMIT].decode("ascii", errors="replace").split()
        if len(parts) != 3 or parts[0] != GREETING or not hmac.compare_digest(parts[1], token):
            await refuse("not allowed")
            return
        key = parts[2]
        if not KEY.match(key):
            await refuse("unknown sandbox")
            return
        try:
            host, port = await address(key)
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), CONNECT_SECONDS
            )
        except SandboxError as error:
            await refuse(str(error))
            return
        except (OSError, TimeoutError) as error:
            await refuse(f"the sandbox does not answer: {error}")
            return
        writer.write(b"OK\n")
        await writer.drain()
        await asyncio.gather(pipe(reader, upstream_writer), pipe(upstream_reader, writer))

    return handle


async def start_relay(sandboxes: Sandboxes, token: str, host: str = "0.0.0.0", port: int = 2222) -> asyncio.Server:
    return await asyncio.start_server(relay_handler(token, sandboxes.address), host, port)
