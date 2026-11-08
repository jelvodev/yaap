"""The gate in front of YAAP's API: every route under /api/ needs a browser session or an API token.

Open are only the routes a browser needs before it is signed in (the sign-in state, signing in, the first-run password
screen) and the health check. A browser session is the HttpOnly cookie; a request that changes something must also send
the session's CSRF token in the X-CSRF-Token header. Where a request comes from (its Host and Origin) was already
checked in front of this gate, for every route (app/auth/boundary.py). An API token of purpose
`api` (Authorization: Bearer) opens the whole API without CSRF, as no browser sends it by itself.

YAAP's own MCP endpoint (/mcp) and the A2A endpoints check their own tokens, and so do a plugin's page files: a
sandboxed frame sends no cookies, so their address carries a token that is random per start. The built frontend is
open, it holds no data.

A plugin page never calls the API itself: YAAP's own page makes its calls with the session and names the plugin
in the X-YAAP-Plugin header. Such a call reaches only the routes the plugin's manifest lists
(app/plugins/page_access.py), on top of the session's own checks, so the header can only narrow what a request may do.
A sandboxed frame cannot send it: it cannot reach YAAP's API with the session, as it has no cookies and the boundary
refuses its opaque origin for anything but the page's static files.
"""

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import anyio

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.auth.account import find_session, password_is_set, same
from app.auth.tokens import TOKEN_PATHS, ApiToken, bearer_value, verify_token
from app.plugins.page_access import PAGE_FILE, PLUGIN_HEADER, PageGrants, page_refusal

OPEN_PATHS = frozenset(
    {
        "/api/v1/health",
        "/api/v1/auth/state",
        "/api/v1/auth/sign-in",
        "/api/v1/auth/setup",
    }
)
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CSRF_HEADER = "x-csrf-token"


def cookie_suffix(host: str | None) -> str:
    """Cookies are not separated by port, so two YAAPs on one computer (8080 and a development copy) would sign each
    other out; the port of the address the browser uses goes into the cookie names."""
    if not host:
        return ""
    port = urlsplit(f"//{host}").port
    return f"_{port}" if port else ""


def session_cookie_name(request: Request) -> str:
    return "yaap_session" + cookie_suffix(request.headers.get("host"))


def csrf_cookie_name(request: Request) -> str:
    return "yaap_csrf" + cookie_suffix(request.headers.get("host"))


@dataclass(frozen=True)
class Identity:
    """Who a request comes from: a signed-in browser or an API token."""

    method: str  # "session" or "token"
    session_id: str | None = None
    csrf_hash: str | None = None
    token: ApiToken | None = None


def bearer(request: Request) -> str | None:
    return bearer_value(request)


def token_path(path: str) -> bool:
    """Routes that check their own token (YAAP's MCP endpoint, the A2A agent)."""
    return any(path == prefix or path.startswith(prefix.rstrip("/") + "/") for prefix in TOKEN_PATHS)


def plugin_page(path: str, method: str) -> bool:
    """A plugin page's files, which check their own page token."""
    return method in SAFE_METHODS and PAGE_FILE.match(path) is not None


def identify(request: Request, used_by: str | None = None) -> Identity | None:
    """The session or token of a request, without deciding whether it may do what it asks."""
    session_factory = request.app.state.session_factory
    presented = bearer(request)
    with session_factory() as session:
        if presented is not None:
            token = verify_token(session, presented, used_by)
            return Identity("token", token=token) if token is not None else None
        value = request.cookies.get(session_cookie_name(request))
        if not value:
            return None
        record = find_session(session, value)
        if record is None:
            return None
        return Identity("session", session_id=record.id, csrf_hash=record.csrf_hash)


def plugin_call_refusal(request: Request) -> JSONResponse | None:
    """A call YAAP's page makes for a plugin page (X-YAAP-Plugin): refused unless the plugin's manifest lists it."""
    plugin = request.headers.get(PLUGIN_HEADER)
    if plugin is None:
        return None
    plugins: PageGrants = request.app.state.plugins
    reason = page_refusal(plugins.page_grant(plugin), request.method, request.url.path)
    return refusal(403, reason) if reason is not None else None


def refusal(status_code: int, detail: str, **headers: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status_code, headers=headers or None)


def check(request: Request) -> tuple[Identity | None, JSONResponse | None]:
    """The identity of an API request, or the response that refuses it."""
    identity = identify(request)
    if identity is None:
        if bearer(request) is not None:
            return None, refusal(401, "This API token is not valid (revoked, expired or mistyped).", **{"WWW-Authenticate": "Bearer"})
        with request.app.state.session_factory() as session:
            ready = password_is_set(session)
        detail = "Sign in to YAAP." if ready else "Set a password for YAAP first."
        return None, refusal(401, detail)
    if identity.method == "token":
        assert identity.token is not None
        if identity.token.purpose != "api":
            return None, refusal(403, f"This token is for the {identity.token.purpose.upper()} endpoint, not the REST API.")
        return identity, None
    if request.method not in SAFE_METHODS:
        header = request.headers.get(CSRF_HEADER, "")
        if not header or identity.csrf_hash is None or not same(header, identity.csrf_hash):
            return None, refusal(403, "The CSRF token is missing or wrong; reload the page.")
    return identity, None


class AuthGate:
    """Pure ASGI middleware, so event streams pass through untouched."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or not path.startswith("/api/"):
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        # Before the open routes: a call for a plugin page never reaches the account routes, signed in or not.
        refused = plugin_call_refusal(request)
        if refused is not None:
            await refused(scope, receive, send)
            return
        if path in OPEN_PATHS or token_path(path) or plugin_page(path, request.method):
            await self.app(scope, receive, send)
            return
        # The database may wait for a writer (a session touch, a token use); that waits beside the event loop.
        identity, response = await anyio.to_thread.run_sync(check, request)
        if response is not None:
            await response(scope, receive, send)
            return
        state: dict[str, Any] = scope.setdefault("state", {})
        state["identity"] = identity
        await self.app(scope, receive, send)


def request_identity(request: Request) -> Identity | None:
    return getattr(request.state, "identity", None)
