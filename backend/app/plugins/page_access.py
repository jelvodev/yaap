"""What a plugin's page may call on YAAP's API: the routes its manifest lists, and nothing else.

A plugin page never calls YAAP itself: it runs in a sandboxed frame on an opaque origin and holds no token. Its SDK
posts each call to YAAP's own page around the frame, which checks it against the manifest and makes it with the
user's session, naming the plugin in the `X-YAAP-Plugin` header. The gate (`app/auth/gate.py`) checks every request
that carries that header with `page_refusal()` as well: the request's method and path must match one of the
manifest's `api` routes, written as in YAAP's OpenAPI description (`GET /api/v1/environments/{environment_id}/projects`),
where a `{name}` stands for exactly one path segment. The manifest is the boundary, not the SDK.

Some routes are never open to a page, listed or not: the account, sessions and API tokens (`/api/v1/auth/`), and the
plugin routes themselves (`/api/v1/plugins`), which hand out the page tokens and restart YAAP.
"""

import re
from dataclasses import dataclass
from typing import Literal, Protocol

# The header with which YAAP's page names the plugin it makes a call for; the gate holds such a call to the manifest.
PLUGIN_HEADER = "x-yaap-plugin"
# The SDK a page imports, served next to the built frontend (app/api/plugins.py).
SDK_SCRIPT = "/plugin-sdk.js"
# A plugin page's files: the address carries a token, random per start, that guards them (app/api/plugins.py).
PAGE_FILE = re.compile(r"^/api/v1/plugins/[^/]+/page/[^/]+/")
ApiMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
API_METHODS: tuple[ApiMethod, ...] = ("GET", "POST", "PUT", "PATCH", "DELETE")
API_PREFIX = "/api/v1/"
# Never open to a page token, so a page cannot manage the account, make tokens, read other pages' tokens or switch
# plugins.
RESERVED_PREFIXES: tuple[str, ...] = ("/api/v1/auth", "/api/v1/plugins")
SEGMENT = re.compile(r"^(?:\{[A-Za-z_][A-Za-z0-9_]*\}|[A-Za-z0-9._~-]+)$")
MAX_ROUTES = 200


class RouteError(ValueError):
    """A manifest route that cannot be used; the message says why."""


@dataclass(frozen=True)
class ApiRoute:
    """One route a page may call: a method and a path template from the OpenAPI description."""

    method: ApiMethod
    path: str

    @property
    def text(self) -> str:
        return f"{self.method} {self.path}"

    def matches(self, method: str, path: str) -> bool:
        if method != self.method:
            return False
        wanted = self.path.split("/")
        given = path.split("/")
        if len(wanted) != len(given):
            return False
        return all(
            (part.startswith("{") and bool(value)) or part == value for part, value in zip(wanted, given, strict=True)
        )


def reserved(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in RESERVED_PREFIXES)


def parse_route(value: str) -> ApiRoute:
    """`GET /api/v1/environments/{environment_id}/projects` as an ApiRoute, or a RouteError saying what is wrong."""
    word, _, path = value.strip().partition(" ")
    path = path.strip()
    method = next((item for item in API_METHODS if item == word), None)
    if method is None:
        raise RouteError(f"{value!r} must start with one of {', '.join(API_METHODS)}.")
    if not path.startswith(API_PREFIX) or path.endswith("/"):
        raise RouteError(f"{value!r} must name a path under {API_PREFIX}, as in YAAP's OpenAPI description.")
    segments = path.split("/")[1:]
    if any(segment in ("", ".", "..") or not SEGMENT.match(segment) for segment in segments):
        raise RouteError(f"{value!r} has a path YAAP cannot match; use the path as the OpenAPI description writes it.")
    if reserved(path):
        raise RouteError(f"{value!r} is not open to plugin pages: the account, tokens and plugins stay with the user.")
    return ApiRoute(method, path)


def parse_routes(value: object) -> tuple[ApiRoute, ...]:
    """The manifest's `api` list; RouteError for anything that is not a list of usable, distinct routes."""
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise RouteError('api must be a list of routes such as "GET /api/v1/environments".')
    if len(value) > MAX_ROUTES:
        raise RouteError(f"api lists more than {MAX_ROUTES} routes.")
    routes = tuple(parse_route(item) for item in value)
    if len({route.text for route in routes}) != len(routes):
        raise RouteError("api names a route twice.")
    return routes


def page_file(path: str) -> bool:
    """The SDK or a file of a plugin page: what a sandboxed page loads by its address."""
    return path == SDK_SCRIPT or PAGE_FILE.match(path) is not None


@dataclass(frozen=True)
class PageGrant:
    """What a loaded plugin's page may call: the plugin and the routes its manifest lists."""

    plugin: str
    routes: tuple[ApiRoute, ...]


class PageGrants(Protocol):
    """Where the gate looks up the plugin a call is made for (the PluginHost)."""

    def page_grant(self, plugin: str) -> PageGrant | None: ...


def page_refusal(grant: PageGrant | None, method: str, path: str) -> str | None:
    """None when a call made for a plugin page may go on; otherwise why it is refused."""
    if grant is None:
        return "This plugin is not loaded with a page; it is off, or YAAP has not started it yet."
    segments = path.split("/")[1:]
    if not path.startswith(API_PREFIX) or any(segment in ("", ".", "..") for segment in segments):
        return "A plugin page can only call YAAP's API routes."
    if reserved(path):
        return "A plugin page cannot call the account, token or plugin routes."
    if not any(route.matches(method, path) for route in grant.routes):
        return f"The plugin {grant.plugin} does not list {method} {path} under api in its plugin.json."
    return None
