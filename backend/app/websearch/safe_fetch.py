"""Fetch public web pages without following redirects into private networks."""

import http.client
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

MAX_REDIRECTS = 5


class FetchError(ValueError):
    pass


def public_address(address: str) -> bool:
    """Decide whether an IP address may be reached by a web fetch."""
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def fetch_public(url: str, limit: int) -> tuple[str, str, str]:
    """Resolve and pin a public address for each request and redirect."""
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
        connection = connection_class(parts.hostname, port, timeout=20)
        # Retain the original host for TLS SNI and the Host header while pinning the checked address.
        connection._create_connection = lambda _target, timeout, source: socket.create_connection(  # type: ignore[method-assign]  # Pin DNS for this request.
            (address, port), timeout, source
        )
        try:
            path = parts.path or "/"
            if parts.query:
                path += "?" + parts.query
            connection.request("GET", path, headers={"User-Agent": "YAAP"})
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308) and response.getheader("Location"):
                if redirect == MAX_REDIRECTS:
                    raise FetchError("Too many redirects.")
                url = urljoin(url, response.getheader("Location"))
                continue
            if response.status >= 400:
                raise FetchError(f"HTTP {response.status} {response.reason}")
            return response.getheader("Content-Type", ""), response.read(limit).decode(errors="replace"), url
        finally:
            connection.close()
    raise FetchError("Too many redirects.")
