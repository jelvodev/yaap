"""The preview's decisions: which address it has, who may open it, and which process a request reaches.

A preview is opened by an authenticated YAAP API call that mints an *open* token for one running process with a port
(app/api/preview.py). The browser takes that token to the preview's own address once; the listener checks it, uses it
up and sets a session cookie of the preview's own, valid for that process and port only. Every later request must
carry that cookie, and reaches the process only while it still runs on the same port.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlencode

from app.agent.processes import ManagedProcess, ProcessManager
from app.auth.boundary import host_allowed, host_name
from app.config import ConfigurationError, environment_value
from app.preview import tokens
from app.preview.http import RequestHead, cookie_value, preview_origin_allowed

OPEN_PATH = "/__yaap_preview/open"
COOKIE_NAME = "yaap_preview"
DEFAULT_BIND = "127.0.0.1"


class PreviewError(Exception):
    """A preview that cannot be opened; the API answers 404 or 409 with the message."""

    def __init__(self, message: str, missing: bool = False) -> None:
        super().__init__(message)
        self.missing = missing


@dataclass(frozen=True)
class PreviewConfig:
    """YAAP_PREVIEW_PORT (0 or empty: no preview listener) and the address it binds inside YAAP's process."""

    port: int = 0
    bind: str = DEFAULT_BIND

    @property
    def enabled(self) -> bool:
        return self.port > 0


def parse_port(value: str) -> int:
    text = value.strip()
    if not text:
        return 0
    if not text.isdigit() or int(text) > 65535:
        raise ConfigurationError(f"YAAP_PREVIEW_PORT: '{value}' is not a port number (0 switches the preview off).")
    return int(text)


def config_from_environment() -> PreviewConfig:
    """Off unless YAAP_PREVIEW_PORT is set: docker-compose.yml sets it (8081 by default), tests and a bare
    `uvicorn` run get no second listener. YAAP_PREVIEW_BIND is 0.0.0.0 in the image, where Docker publishes the port
    on 127.0.0.1 only."""
    port = parse_port(environment_value("YAAP_PREVIEW_PORT"))
    bind = environment_value("YAAP_PREVIEW_BIND", DEFAULT_BIND).strip() or DEFAULT_BIND
    return PreviewConfig(port, bind)


@dataclass(frozen=True)
class PreviewLink:
    url: str
    process_id: int
    port: int
    expires_at: datetime


@dataclass(frozen=True)
class Refusal:
    status_code: int
    detail: str


BAD_HOST = Refusal(400, "This address is not one the preview answers on.")
FOREIGN_ORIGIN = Refusal(403, "Only the preview's own pages can make requests to it.")
NOT_OPENED = Refusal(403, "This preview is not open. Open it from the preview panel in YAAP.")
LINK_INVALID = Refusal(403, "This preview link is not valid (expired, used or for another process). Open it again.")


def link_authority(host_header: str | None, port: int) -> str:
    """The preview's address on the same host name the browser uses for YAAP, so it shares nothing but the name."""
    name = host_name(host_header or "") or "localhost"
    shown = f"[{name}]" if ":" in name else name
    return f"{shown}:{port}"


def request_refusal(head: RequestHead, allowed: frozenset[str], port: int) -> Refusal | None:
    """The preview's own boundary: an allowed host (no DNS rebinding) and no origin but its own."""
    if not host_allowed(head.get("host"), allowed):
        return BAD_HOST
    if not preview_origin_allowed(head.get("origin"), allowed, port):
        return FOREIGN_ORIGIN
    return None


@dataclass
class PreviewService:
    """The preview of this YAAP: its configuration, the signing key of this start, and the used open tokens."""

    processes: ProcessManager
    config: PreviewConfig
    allowed: frozenset[str]
    key: bytes = field(default_factory=tokens.new_key)
    clock: Callable[[], float] = time.time
    used: dict[str, int] = field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def running(self, process_id: int, port: int | None = None) -> ManagedProcess | None:
        managed = self.processes.processes.get(process_id)
        if managed is None or managed.status != "running" or managed.port is None:
            return None
        return managed if port is None or managed.port == port else None

    def link(self, process_id: int, project_id: int, host_header: str | None) -> PreviewLink:
        """An open link for a running process of this project with a port; minted by an authenticated API call."""
        if not self.enabled:
            raise PreviewError("The preview is switched off (YAAP_PREVIEW_PORT is 0 or not set).")
        managed = self.running(process_id)
        if managed is None or managed.project_id != project_id or managed.port is None:
            raise PreviewError("No running process with a port and this id in the project.", missing=True)
        grant = tokens.new_grant("open", managed.id, managed.port, self.clock(), tokens.OPEN_SECONDS)
        query = urlencode({"process": managed.id, "token": tokens.mint(self.key, grant)})
        url = f"http://{link_authority(host_header, self.config.port)}{OPEN_PATH}?{query}"
        return PreviewLink(url, managed.id, managed.port, datetime.fromtimestamp(grant.expires, UTC))

    def open_session(self, query: str) -> str | None:
        """The session cookie's value for a valid, unused open token naming the process it is for; None otherwise."""
        values = parse_qs(query)
        process = (values.get("process") or [""])[0]
        now = self.clock()
        grant = tokens.read(self.key, (values.get("token") or [""])[0], "open", now)
        if grant is None or not process.isdigit() or int(process) != grant.process_id:
            return None
        self.forget_expired(now)
        if grant.nonce in self.used or self.running(grant.process_id, grant.port) is None:
            return None
        self.used[grant.nonce] = grant.expires
        session = tokens.new_grant("session", grant.process_id, grant.port, now, tokens.SESSION_SECONDS)
        return tokens.mint(self.key, session)

    def forget_expired(self, now: float) -> None:
        for nonce in [nonce for nonce, expires in self.used.items() if expires <= now]:
            del self.used[nonce]

    def admitted(self, head: RequestHead) -> ManagedProcess | None:
        """The process a request with a valid preview cookie reaches, while it runs on the cookie's port."""
        grant = tokens.read(self.key, cookie_value(head, COOKIE_NAME), "session", self.clock())
        return self.running(grant.process_id, grant.port) if grant is not None else None


def session_cookie(value: str) -> str:
    """The preview's own cookie: HttpOnly, SameSite=Strict, for the whole preview address, gone with the browser."""
    return f"{COOKIE_NAME}={value}; Path=/; HttpOnly; SameSite=Strict"
