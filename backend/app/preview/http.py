"""The HTTP heads the preview proxy reads and rewrites; pure functions, so every rule has a table-driven test.

The proxy passes bodies and WebSocket frames through as bytes and only rewrites the heads: the request head on its
way to the dev server (YAAP's cookies and every credential header removed, `Host` and `Origin` set to the dev
server's own) and the response head on its way back (cookies named like YAAP's dropped). Every connection carries one
request (`Connection: close` both ways), so the proxy never has to find where a body ends; a WebSocket upgrade keeps
its connection open after the `101` and is relayed as bytes.
"""

from dataclasses import dataclass, field
from urllib.parse import urlsplit

from app.auth.boundary import ORIGIN_SCHEMES, host_allowed

MAX_HEAD_BYTES = 64 * 1024
HEAD_END = b"\r\n\r\n"
# Cookies whose name starts with this belong to YAAP (its session and CSRF token, the preview's own cookie) and never
# reach a dev server, nor may a dev server set them.
YAAP_COOKIE_PREFIX = "yaap_"
# Credential and connection headers a dev server never gets. Bodies stay framed as the browser sent them, so
# Content-Length and Transfer-Encoding pass unchanged.
DROPPED_REQUEST_HEADERS = frozenset(
    {"authorization", "proxy-authorization", "proxy-connection", "connection", "keep-alive", "te", "upgrade"}
)
DROPPED_RESPONSE_HEADERS = frozenset({"connection", "keep-alive", "proxy-authenticate", "proxy-connection", "upgrade"})
SWITCHING_PROTOCOLS = 101


class HeadError(ValueError):
    """A head the proxy cannot read; the client gets 400."""


Headers = list[tuple[str, str]]


@dataclass(frozen=True)
class RequestHead:
    method: str
    target: str
    version: str
    headers: Headers = field(default_factory=list)

    def get(self, name: str) -> str | None:
        return header(self.headers, name)

    @property
    def path(self) -> str:
        return urlsplit(self.target).path or "/"

    @property
    def query(self) -> str:
        return urlsplit(self.target).query

    @property
    def websocket(self) -> bool:
        """A WebSocket handshake: Upgrade: websocket on a GET (dev servers use one for hot reload)."""
        upgrade = (self.get("upgrade") or "").strip().lower()
        return self.method == "GET" and upgrade == "websocket"


@dataclass(frozen=True)
class ResponseHead:
    version: str
    status: int
    reason: str
    headers: Headers = field(default_factory=list)


def header(headers: Headers, name: str) -> str | None:
    wanted = name.lower()
    return next((value for key, value in headers if key.lower() == wanted), None)


def parse_header_lines(lines: list[str]) -> Headers:
    headers: Headers = []
    for line in lines:
        if not line:
            continue
        if line[0] in " \t" or ":" not in line:
            # Folded header lines are obsolete (RFC 9112 §5.2) and a way to smuggle; refuse them.
            raise HeadError("A header line cannot be read.")
        name, value = line.split(":", 1)
        if not name or name != name.strip() or any(character in name for character in " \t\"(),/;<=>?@[\\]{}"):
            raise HeadError("A header name cannot be read.")
        headers.append((name, value.strip()))
    return headers


def split_head(data: bytes) -> list[str]:
    if len(data) > MAX_HEAD_BYTES:
        raise HeadError("The head is too long.")
    # Latin-1 maps every byte to one character, so any head decodes and serializes back to the same bytes.
    lines = data.decode("latin-1").removesuffix("\r\n\r\n").split("\r\n")
    if not lines or not lines[0]:
        raise HeadError("The head is empty.")
    # A bare CR or LF, or a NUL, would let the proxy and the dev server see different headers (request smuggling).
    if any(character in line for line in lines for character in "\r\n\x00"):
        raise HeadError("The head has a stray line break.")
    return lines


def parse_request_head(data: bytes) -> RequestHead:
    lines = split_head(data)
    parts = lines[0].split(" ")
    if len(parts) != 3 or not parts[0].isalpha() or not parts[2].startswith("HTTP/1."):
        raise HeadError("The request line cannot be read.")
    method, target, version = parts
    if not target.startswith("/"):
        raise HeadError("The preview answers only for paths on its own address.")
    headers = parse_header_lines(lines[1:])
    lengths = [value for name, value in headers if name.lower() == "content-length"]
    if len(lengths) > 1 or (lengths and header(headers, "transfer-encoding") is not None):
        raise HeadError("The request's body length is ambiguous.")
    return RequestHead(method.upper(), target, version, headers)


def parse_response_head(data: bytes) -> ResponseHead:
    lines = split_head(data)
    parts = lines[0].split(" ", 2)
    if len(parts) < 2 or not parts[0].startswith("HTTP/1.") or not parts[1].isdigit():
        raise HeadError("The dev server's status line cannot be read.")
    return ResponseHead(parts[0], int(parts[1]), parts[2] if len(parts) == 3 else "", parse_header_lines(lines[1:]))


def serialize_request(head: RequestHead) -> bytes:
    lines = [f"{head.method} {head.target} {head.version}", *(f"{name}: {value}" for name, value in head.headers)]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")


def serialize_response(head: ResponseHead) -> bytes:
    lines = [f"{head.version} {head.status} {head.reason}".rstrip(), *(f"{n}: {v}" for n, v in head.headers)]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")


def yaap_cookie(name: str) -> bool:
    return name.strip().lower().startswith(YAAP_COOKIE_PREFIX)


def without_yaap_cookies(value: str) -> str:
    """A Cookie header's pairs without YAAP's; empty when nothing else was in it."""
    kept = [pair.strip() for pair in value.split(";") if pair.strip() and not yaap_cookie(pair.split("=", 1)[0])]
    return "; ".join(kept)


def cookie_value(head: RequestHead, name: str) -> str | None:
    """The value of a cookie in the request's Cookie headers, the last one when it appears more than once."""
    found = None
    for key, value in head.headers:
        if key.lower() != "cookie":
            continue
        for pair in value.split(";"):
            cookie, _, content = pair.strip().partition("=")
            if cookie == name:
                found = content
    return found


def connection_named(headers: Headers) -> set[str]:
    """Headers the Connection header names as hop-by-hop."""
    value = header(headers, "connection") or ""
    return {item.strip().lower() for item in value.split(",") if item.strip()}


def same_place(url: str, authority: str) -> str:
    """An Origin or Referer pointed at the dev server's own address, so it sees requests from its own pages."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return f"http://{authority}"
    rest = parts.path + (f"?{parts.query}" if parts.query else "")
    return f"http://{authority}{rest}"


def upstream_request(head: RequestHead, authority: str) -> RequestHead:
    """The request as the dev server gets it: without YAAP's cookies, credential and hop-by-hop headers, with its own
    Host and Origin, and with `Connection: close` (or `Upgrade` for a WebSocket handshake)."""
    dropped = DROPPED_REQUEST_HEADERS | (connection_named(head.headers) - {"upgrade"})
    headers: Headers = []
    for name, value in head.headers:
        lower = name.lower()
        if lower in dropped or lower == "host":
            continue
        if lower == "cookie":
            value = without_yaap_cookies(value)
            if not value:
                continue
        elif lower in ("origin", "referer"):
            value = same_place(value, authority) if lower == "referer" else f"http://{authority}"
        headers.append((name, value))
    headers.insert(0, ("Host", authority))
    if head.websocket:
        headers += [("Connection", "Upgrade"), ("Upgrade", head.get("upgrade") or "websocket")]
    else:
        headers.append(("Connection", "close"))
    return RequestHead(head.method, head.target, "HTTP/1.1", headers)


def set_cookie_name(value: str) -> str:
    return value.split(";", 1)[0].split("=", 1)[0]


def client_response(head: ResponseHead) -> ResponseHead:
    """The dev server's response head as the browser gets it: no cookie named like YAAP's, no hop-by-hop headers, and
    `Connection: close`, except for a WebSocket's `101` which keeps the connection for the frames."""
    upgraded = head.status == SWITCHING_PROTOCOLS
    dropped = DROPPED_RESPONSE_HEADERS | connection_named(head.headers)
    headers: Headers = []
    for name, value in head.headers:
        lower = name.lower()
        if lower in dropped and not (upgraded and lower == "upgrade"):
            continue
        if lower == "set-cookie" and yaap_cookie(set_cookie_name(value)):
            continue
        headers.append((name, value))
    headers.append(("Connection", "Upgrade" if upgraded else "close"))
    return ResponseHead(head.version, head.status, head.reason, headers)


def preview_origin_allowed(origin: str | None, allowed: frozenset[str], port: int) -> bool:
    """A request to the preview may name only the preview's own origin (an allowed host on the preview's port), or
    none. Another local page (YAAP's own included) cannot reach a dev server through the preview's cookie."""
    if origin is None:
        return True
    try:
        parts = urlsplit(origin)
        origin_port = parts.port
    except ValueError:
        return False
    if parts.scheme.lower() not in ORIGIN_SCHEMES or parts.path or parts.query or parts.fragment:
        return False
    return origin_port == port and host_allowed(parts.netloc, allowed)
