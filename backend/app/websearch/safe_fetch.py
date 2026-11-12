"""Fetch public web pages without following redirects into private networks."""

import http.client
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

MAX_REDIRECTS = 5


class FetchError(ValueError):
    """A fetch that was refused or failed; `status` is the HTTP status when the server answered with an error."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def public_address(address: str) -> bool:
    """Decide whether an IP address may be reached by a web fetch."""
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def request_origin(url: str) -> tuple[str, str | None, int | None]:
    """Scheme, host and port of a URL: the origin that may receive a caller's own headers."""
    parts = urlsplit(url)
    return parts.scheme, parts.hostname, parts.port


def fetch_public(url: str, limit: int) -> tuple[str, str, str]:
    """Fetch a public page as text: its content type, the text and the final URL."""
    content_type, body, final_url = fetch_public_bytes(url, limit)
    return content_type, body.decode(errors="replace"), final_url


def fetch_public_bytes(
    url: str, limit: int, *, timeout: float = 20, headers: dict[str, str] | None = None
) -> tuple[str, bytes, str]:
    """Resolve and pin a public address for each request and redirect; returns type, up to `limit` bytes, final URL.

    `headers` replace the default User-Agent for the requested origin only (scheme, host and port): after a redirect
    elsewhere, or from https to http, they are dropped, so a token cannot leak.
    """
    origin = request_origin(url)
    for redirect in range(MAX_REDIRECTS + 1):
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username is not None:
            raise FetchError("Only public http and https URLs without credentials can be fetched.")
        port = parts.port or (443 if parts.scheme == "https" else 80)
        addresses = socket.getaddrinfo(parts.hostname, port, type=socket.SOCK_STREAM)
        if not addresses or any(not public_address(item[4][0]) for item in addresses):
            raise FetchError("The destination is not a public address.")
        address = addresses[0][4][0]
        connection_class = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
        connection = connection_class(parts.hostname, port, timeout=timeout)
        # Retain the original host for TLS SNI and the Host header while pinning the checked address.
        connection._create_connection = lambda _target, timeout, source: socket.create_connection(  # type: ignore[method-assign]  # Pin DNS for this request.
            (address, port), timeout, source
        )
        try:
            path = parts.path or "/"
            if parts.query:
                path += "?" + parts.query
            sent = headers if headers is not None and request_origin(url) == origin else {"User-Agent": "YAAP"}
            connection.request("GET", path, headers=sent)
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308) and response.getheader("Location"):
                if redirect == MAX_REDIRECTS:
                    raise FetchError("Too many redirects.")
                url = urljoin(url, response.getheader("Location"))
                continue
            if response.status >= 400:
                raise FetchError(f"HTTP {response.status} {response.reason}", response.status)
            return response.getheader("Content-Type", ""), response.read(limit), url
        finally:
            connection.close()
    raise FetchError("Too many redirects.")
