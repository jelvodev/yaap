"""How YAAP talks to the yaap-sandbox helper: its HTTP API and its SSH relay.

The helper's address comes from YAAP_SANDBOX_URL (docker-compose.yml) and its token from the file the helper writes
into the volume both mount. Without either, or when the helper does not answer, sandboxes and runners are not
available and YAAP says how to start the helper.
"""

import asyncio
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.config import environment_value
from app.targets.base import TargetError

TOKEN_FILE = "/run/yaap-sandbox/token"
RELAY_PORT = 2222
GREETING = "YAAP-SANDBOX"
NOT_ENABLED = (
    "The sandbox helper is not running. Start it with docker compose up -d --build in YAAP's folder (see "
    "docs/install.md)."
)


class SandboxUnavailable(TargetError):
    """The helper is not configured, not running, or refused the request; the message is safe to show."""


class HelperClient:
    def __init__(
        self,
        url: str,
        token: Callable[[], str | None],
        transport: httpx.AsyncBaseTransport | None = None,
        relay: tuple[str, int] | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.transport = transport
        host = urlsplit(self.url).hostname or ""
        self.relay = relay or (host, RELAY_PORT)

    @classmethod
    def from_environment(cls) -> "HelperClient | None":
        url = environment_value("YAAP_SANDBOX_URL").strip()
        if not url:
            return None
        path = Path(environment_value("YAAP_SANDBOX_TOKEN_FILE", TOKEN_FILE).strip() or TOKEN_FILE)

        def read_token() -> str | None:
            try:
                return path.read_text(encoding="utf-8").strip() or None
            except OSError:
                return None

        return cls(url, read_token)

    def secret(self) -> str:
        token = self.token()
        if not token:
            raise SandboxUnavailable(NOT_ENABLED)
        return token

    async def call(self, method: str, path: str, json: dict[str, Any] | None = None, timeout: float = 60) -> Any:
        headers = {"Authorization": f"Bearer {self.secret()}"}
        # A client per call: replies, the files tab and the idle pass run in different event loops in tests.
        async with httpx.AsyncClient(base_url=self.url, transport=self.transport, timeout=timeout) as client:
            try:
                response = await client.request(method, path, json=json, headers=headers)
            except httpx.HTTPError as error:
                raise SandboxUnavailable(f"{NOT_ENABLED} ({error.__class__.__name__})") from error
        if response.status_code >= 400:
            try:
                detail = str(response.json().get("detail") or response.text)
            except ValueError:
                detail = response.text
            raise SandboxUnavailable(f"The sandbox helper refused: {detail}")
        return response.json()

    async def start(self, key: str, folder: str, network: bool, authorized_key: str, host_key: str) -> dict[str, Any]:
        body = {"folder": folder, "network": network, "authorized_key": authorized_key, "host_key": host_key}
        return await self.call("POST", f"/sandboxes/{key}/start", body, timeout=180)

    async def stop(self, key: str) -> dict[str, Any]:
        return await self.call("POST", f"/sandboxes/{key}/stop")

    async def remove(self, key: str) -> dict[str, Any]:
        return await self.call("DELETE", f"/sandboxes/{key}")

    async def status(self, key: str) -> dict[str, Any]:
        return await self.call("GET", f"/sandboxes/{key}")

    async def list_all(self) -> list[dict[str, Any]]:
        return await self.call("GET", "/sandboxes")

    async def health(self) -> dict[str, Any]:
        return await self.call("GET", "/health", timeout=10)

    async def host_data(self) -> str:
        """The source of YAAP's /data mount as Docker reports it; empty when the helper cannot find it."""
        answer = await self.call("GET", "/host/data", timeout=10)
        return str(answer.get("source") or "") if isinstance(answer, dict) else ""

    async def open_socket(self, key: str) -> socket.socket:
        """A socket to the sandbox's sshd through the helper's relay, ready for asyncssh."""
        token = self.secret()
        return await asyncio.to_thread(relay_socket, self.relay, token, key)


def relay_socket(relay: tuple[str, int], token: str, key: str, timeout: float = 15) -> socket.socket:
    try:
        sock = socket.create_connection(relay, timeout=timeout)
    except OSError as error:
        raise SandboxUnavailable(f"{NOT_ENABLED} ({error})") from error
    try:
        sock.sendall(f"{GREETING} {token} {key}\n".encode())
        # Byte by byte up to the newline, so nothing of sshd's own greeting is taken from asyncssh.
        answer = b""
        while not answer.endswith(b"\n") and len(answer) < 512:
            chunk = sock.recv(1)
            if not chunk:
                break
            answer += chunk
    except OSError as error:
        sock.close()
        raise SandboxUnavailable(f"The sandbox relay did not answer: {error}") from error
    text = answer.decode("utf-8", errors="replace").strip()
    if text != "OK":
        sock.close()
        raise SandboxUnavailable(f"The sandbox relay refused: {text.removeprefix('ERR ') or 'no answer'}")
    sock.settimeout(None)
    sock.setblocking(False)
    return sock
