"""The browser boundary in front of everything YAAP serves: the API, the built frontend, /mcp, /a2a, /mcp/outside and
plugin pages.

Two decisions, both pure functions:

- The `Host` header must name an allowed host: `localhost`, `127.0.0.1` or `[::1]` on any port, or a name in
  YAAP_ALLOWED_HOSTS. A web page that points its own domain at 127.0.0.1 (DNS rebinding) sends its own name as `Host`,
  so it is refused before any route sees the request.
- A request that names its `Origin` must come from an allowed host. Browsers send an `Origin` with every request that
  changes something, with cross-origin reads and with WebSockets, and leave it out of same-origin page loads; programs
  (curl, Claude Code and Codex calling /mcp, scripts with an API token) send none, so a request without `Origin` passes.
- The preview's origin is refused explicitly. The preview of a project's app (app/preview) runs on a port of its own
  on the same local host names, so its origin would pass the host check; but its pages are the project's code, and
  browsers send YAAP's SameSite cookies to them (a site does not depend on the port). Any origin on the preview's
  port is therefore refused, so such a page can neither change anything in YAAP's API nor open a WebSocket to it.

One narrow exception, for plugin pages: a page runs in a sandboxed frame on an opaque origin, and the browser loads
its ES modules (the page's own and the plugin SDK) in CORS mode, which sends `Origin: null`. A `GET` or `HEAD` of
`/plugin-sdk.js` or of a file under a page's address passes with `Origin: null`; those are static files, and a page's
address carries a token that is random per start. Every other request from an opaque origin is refused, the API
included: a page reaches the API only through YAAP's own page.

The Vite dev server proxies /api with the browser's own `Host` and `Origin` (localhost:5173), and the internal MCP
address is 127.0.0.1, so both stay inside the allowlist.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import ConfigurationError
from app.plugins.page_access import page_file

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
ORIGIN_SCHEMES = frozenset({"http", "https"})
SEPARATORS = re.compile(r"[\s,]+")
# A WebSocket refused before it is accepted: "policy violation"; the server answers the handshake with 403.
POLICY_VIOLATION = 1008
# What a sandboxed frame (a plugin page) sends as its origin.
OPAQUE_ORIGIN = "null"
STATIC_FILE_METHODS = frozenset({"GET", "HEAD"})


@dataclass(frozen=True)
class Refusal:
    status_code: int
    detail: str


BAD_HOST = Refusal(
    400, "This address is not one YAAP answers on. Open YAAP on localhost, or add the name to YAAP_ALLOWED_HOSTS."
)
FOREIGN_ORIGIN = Refusal(403, "Requests from another site's page are refused.")


def host_name(authority: str) -> str | None:
    """The lowercased name in a `Host` header or a URL's authority, without port; None when it is malformed."""
    if not authority or any(character in authority for character in "/?#@\\ "):
        return None
    try:
        parts = urlsplit(f"//{authority}")
        parts.port  # noqa: B018 - reading the port validates it (ValueError for a port that is not a number)
    except ValueError:
        return None
    return parts.hostname or None


def parse_allowed_hosts(value: str) -> frozenset[str]:
    """The names in YAAP_ALLOWED_HOSTS, separated by commas or spaces; a port in an entry is ignored."""
    names: set[str] = set()
    for entry in SEPARATORS.split(value.strip()):
        if not entry:
            continue
        name = host_name(entry)
        if name is None:
            raise ConfigurationError(f"YAAP_ALLOWED_HOSTS: '{entry}' is not a host name.")
        names.add(name)
    return frozenset(names)


def allowed_hosts(extra: Iterable[str] = ()) -> frozenset[str]:
    return LOCAL_HOSTS | frozenset(extra)


def host_allowed(host: str | None, allowed: frozenset[str]) -> bool:
    name = host_name(host) if host is not None else None
    return name is not None and name in allowed


def origin_allowed(origin: str | None, allowed: frozenset[str], refused_ports: frozenset[int] = frozenset()) -> bool:
    """No `Origin` passes; `null` (a sandboxed frame, a file), anything that is not http(s) on an allowed host, and an
    origin on a refused port (the preview's) do not."""
    if origin is None:
        return True
    try:
        parts = urlsplit(origin)
        port = parts.port
    except ValueError:
        return False
    if parts.scheme.lower() not in ORIGIN_SCHEMES or parts.path or parts.query or parts.fragment:
        return False
    if port is not None and port in refused_ports:
        return False
    return host_allowed(parts.netloc, allowed)


def refusal_for(
    host: str | None, origin: str | None, allowed: frozenset[str], refused_ports: frozenset[int] = frozenset()
) -> Refusal | None:
    """Why a request is refused at the boundary, or None when it may go on."""
    if not host_allowed(host, allowed):
        return BAD_HOST
    if not origin_allowed(origin, allowed, refused_ports):
        return FOREIGN_ORIGIN
    return None


def opaque_origin_allowed(method: str, path: str) -> bool:
    """Whether a request may come from an opaque origin: only a plugin page loading the SDK or its own files."""
    return method in STATIC_FILE_METHODS and page_file(path)


def request_refusal(
    method: str,
    path: str,
    host: str | None,
    origin: str | None,
    allowed: frozenset[str],
    refused_ports: frozenset[int] = frozenset(),
) -> Refusal | None:
    """Why an HTTP request is refused at the boundary, or None: `refusal_for()`, where a plugin page's static files
    may come from an opaque origin. The Host allowlist applies to them as to everything."""
    if origin == OPAQUE_ORIGIN and opaque_origin_allowed(method, path):
        return refusal_for(host, None, allowed)
    return refusal_for(host, origin, allowed, refused_ports)


class BrowserBoundary:
    """Pure ASGI middleware, outermost, so streams pass through untouched and nothing is served before the check."""

    def __init__(self, app: ASGIApp, allowed: frozenset[str], refused_ports: frozenset[int] = frozenset()) -> None:
        self.app = app
        self.allowed = allowed
        # Origins on these ports are refused although their host is allowed: the preview's port.
        self.refused_ports = refused_ports

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        host, origin = headers.get("host"), headers.get("origin")
        if scope["type"] == "http":
            refusal = request_refusal(scope["method"], scope["path"], host, origin, self.allowed, self.refused_ports)
        else:
            refusal = refusal_for(host, origin, self.allowed, self.refused_ports)
        if refusal is None:
            await self.app(scope, receive, send)
        elif scope["type"] == "websocket":
            await receive()  # the handshake's websocket.connect, answered with a close instead of an accept
            await send({"type": "websocket.close", "code": POLICY_VIOLATION})
        else:
            response = JSONResponse({"detail": refusal.detail}, status_code=refusal.status_code)
            await response(scope, receive, send)
