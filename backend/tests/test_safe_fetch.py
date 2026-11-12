"""Public web fetches cannot reach internal services, including through redirects."""

import socket

import pytest

from app.websearch.safe_fetch import FetchError, fetch_public, public_address, request_origin


@pytest.mark.parametrize(
    ("address", "allowed"),
    [("127.0.0.1", False), ("::1", False), ("10.0.0.1", False), ("169.254.169.254", False),
     ("100.64.0.1", False), ("::ffff:127.0.0.1", False), ("224.0.0.1", False), ("ff02::1", False),
     ("8.8.8.8", True), ("2606:4700:4700::1111", True)],
)
def test_public_address_policy(address: str, allowed: bool) -> None:
    assert public_address(address) is allowed


def test_fetch_rejects_mixed_public_and_private_dns_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 80)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80)),
    ])
    with pytest.raises(FetchError, match="not a public address"):
        fetch_public("http://example.test/", 100)


def test_fetch_rejects_private_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    def resolve(host: str, port: int, **_kwargs: object) -> list[tuple]:
        address = "8.8.8.8" if host == "example.test" else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

    class Response:
        status = 302

        def getheader(self, name: str, default: str = "") -> str:
            return "http://localhost/private" if name == "Location" else default

    class Connection:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def request(self, *_args: object, **_kwargs: object) -> None:
            pass

        def getresponse(self) -> Response:
            return Response()

        def close(self) -> None:
            pass

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr("app.websearch.safe_fetch.http.client.HTTPConnection", Connection)
    with pytest.raises(FetchError, match="not a public address"):
        fetch_public("http://example.test/", 100)


def test_fetch_connects_to_the_checked_address(monkeypatch: pytest.MonkeyPatch) -> None:
    connected: list[tuple[str, int]] = []

    def connect(target: tuple[str, int], _timeout: object, _source: object) -> object:
        connected.append(target)
        return object()

    class Response:
        status = 200

        def getheader(self, _name: str, default: str = "") -> str:
            return default

        def read(self, _limit: int) -> bytes:
            return b"ok"

    class Connection:
        def __init__(self, host: str, port: int, **_kwargs: object) -> None:
            self.host, self.port = host, port

        def request(self, *_args: object, **_kwargs: object) -> None:
            self._create_connection((self.host, self.port), 20, None)

        def getresponse(self) -> Response:
            return Response()

        def close(self) -> None:
            pass

    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 80)),
    ])
    monkeypatch.setattr(socket, "create_connection", connect)
    monkeypatch.setattr("app.websearch.safe_fetch.http.client.HTTPConnection", Connection)
    assert fetch_public("http://example.test/", 100)[1] == "ok"
    assert connected == [("8.8.8.8", 80)]


@pytest.mark.parametrize(
    ("first", "then", "same"),
    [
        ("https://api.github.com/repos/a/b", "https://api.github.com/other", True),
        ("https://api.github.com/x", "https://objects.githubusercontent.com/x", False),
        ("https://api.github.com/x", "http://api.github.com/x", False),
        ("https://api.github.com/x", "https://api.github.com:8443/x", False),
        ("https://API.github.com/x", "https://api.github.com/y", True),
    ],
)
def test_own_headers_only_follow_a_redirect_within_the_same_origin(first: str, then: str, same: bool) -> None:
    assert (request_origin(first) == request_origin(then)) is same
