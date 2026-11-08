"""The preview proxy: tokens, the rewritten heads, the boundary, and requests through the listener."""

import asyncio
import functools
import json
import socket
import sys
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agent.command_tools import command_environment
from app.agent.processes import ManagedProcess, ProcessManager
from app.auth.boundary import FOREIGN_ORIGIN, allowed_hosts, refusal_for
from app.config import ConfigurationError
from app.preview import tokens
from app.preview.http import (
    HeadError,
    RequestHead,
    ResponseHead,
    client_response,
    parse_request_head,
    preview_origin_allowed,
    upstream_request,
)
from app.preview.service import COOKIE_NAME, PreviewService, parse_port
from app.targets.container import CONTAINER

PYTHON = Path(sys.executable).as_posix()
DEV_SERVER = Path(__file__).with_name("preview_dev_server.py").as_posix()
KEY = b"k" * 32
ALLOWED = allowed_hosts({"testserver"})


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


# --- Tokens -----------------------------------------------------------------------------------------------------------


def grant(
    kind: tokens.TokenKind = "open", process_id: int = 3, port: int = 5173, expires: int = 1000
) -> tokens.PreviewGrant:
    return tokens.PreviewGrant(kind, process_id, port, expires, "nonce")


SIGNATURE = tokens.mint(KEY, grant()).split(".")[1]


@pytest.mark.parametrize(
    ("token", "kind", "now", "expected"),
    [
        (tokens.mint(KEY, grant()), "open", 999, grant()),
        (tokens.mint(KEY, grant()), "open", 1000, None),  # expired at its second
        (tokens.mint(KEY, grant()), "session", 999, None),  # another kind
        (tokens.mint(b"x" * 32, grant()), "open", 999, None),  # another key (another YAAP start)
        (tokens.mint(KEY, grant())[:-2] + "AA", "open", 999, None),  # tampered signature
        # The payload of one token with the signature of another.
        (tokens.mint(KEY, grant(process_id=4)).split(".")[0] + "." + SIGNATURE, "open", 9, None),
        ("", "open", 0, None),
        (None, "open", 0, None),
        ("not.a.token", "open", 0, None),
        ("x" * 600, "open", 0, None),
    ],
)
def test_token_decision(
    token: str | None, kind: tokens.TokenKind, now: float, expected: tokens.PreviewGrant | None
) -> None:
    assert tokens.read(KEY, token, kind, now) == expected


# --- Heads ------------------------------------------------------------------------------------------------------------


def request(*headers: tuple[str, str], method: str = "GET", target: str = "/app") -> RequestHead:
    return RequestHead(method, target, "HTTP/1.1", list(headers))


def test_the_dev_server_gets_no_yaap_cookie_and_no_credential() -> None:
    sent = upstream_request(
        request(
            ("Host", "localhost:8081"),
            ("Cookie", "yaap_session_8080=s; theme=dark; yaap_csrf_8080=c; YAAP_PREVIEW=p"),
            ("Cookie", "yaap_preview=only"),
            ("Authorization", "Bearer secret"),
            ("Proxy-Authorization", "Basic x"),
            ("Origin", "http://localhost:8081"),
            ("Referer", "http://localhost:8081/page?a=1"),
            ("Connection", "keep-alive, X-Private"),
            ("X-Private", "hop"),
            ("Accept", "text/html"),
        ),
        "localhost:5173",
    )
    assert sent.headers == [
        ("Host", "localhost:5173"),
        ("Cookie", "theme=dark"),
        ("Origin", "http://localhost:5173"),
        ("Referer", "http://localhost:5173/page?a=1"),
        ("Accept", "text/html"),
        ("Connection", "close"),
    ]


def test_a_websocket_handshake_keeps_its_upgrade() -> None:
    handshake = request(("Upgrade", "websocket"), ("Connection", "Upgrade"), ("Sec-WebSocket-Protocol", "vite-hmr"))
    sent = upstream_request(handshake, "localhost:5173")
    assert ("Sec-WebSocket-Protocol", "vite-hmr") in sent.headers
    assert sent.headers[-2:] == [("Connection", "Upgrade"), ("Upgrade", "websocket")]


@pytest.mark.parametrize(
    ("status", "expected_connection"),
    [(200, "close"), (304, "close"), (101, "Upgrade")],
)
def test_the_browser_gets_no_cookie_named_like_yaaps(status: int, expected_connection: str) -> None:
    head = ResponseHead(
        "HTTP/1.1",
        status,
        "",
        [
            ("Set-Cookie", "app=1; Path=/"),
            ("Set-Cookie", "yaap_session_8080=planted; Path=/"),
            ("Set-Cookie", " Yaap_csrf=x"),
            ("Upgrade", "websocket"),
            ("Keep-Alive", "timeout=5"),
            ("Connection", "keep-alive"),
        ],
    )
    out = client_response(head)
    assert [value for name, value in out.headers if name == "Set-Cookie"] == ["app=1; Path=/"]
    assert out.headers[-1] == ("Connection", expected_connection)
    assert (("Upgrade", "websocket") in out.headers) is (status == 101)


@pytest.mark.parametrize(
    "data",
    [
        b"GET http://evil.example/ HTTP/1.1\r\nHost: x\r\n\r\n",  # an absolute target: not a path on the preview
        b"GET / HTTP/1.1\r\nHost: x\r\n folded\r\n\r\n",
        b"GET / HTTP/1.1\r\nHost: x\nX-Smuggled: 1\r\n\r\n",
        b"POST / HTTP/1.1\r\nContent-Length: 1\r\nTransfer-Encoding: chunked\r\n\r\n",
        b"POST / HTTP/1.1\r\nContent-Length: 1\r\nContent-Length: 2\r\n\r\n",
        b"GET / HTTP/2\r\n\r\n",
        b"\r\n\r\n",
    ],
)
def test_heads_that_could_be_read_two_ways_are_refused(data: bytes) -> None:
    with pytest.raises(HeadError):
        parse_request_head(data)


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        (None, True),
        ("http://localhost:8081", True),
        ("http://testserver:8081", True),
        ("http://localhost:8080", False),  # YAAP's own page
        ("http://localhost", False),
        ("http://evil.example:8081", False),
        ("null", False),
        ("http://localhost:8081/path", False),
    ],
)
def test_the_preview_takes_requests_only_from_its_own_pages(origin: str | None, expected: bool) -> None:
    assert preview_origin_allowed(origin, ALLOWED, 8081) is expected


@pytest.mark.parametrize(("value", "expected"), [("", 0), ("0", 0), (" 8081 ", 8081)])
def test_the_preview_port_is_read_and_0_switches_it_off(value: str, expected: int) -> None:
    assert parse_port(value) == expected


@pytest.mark.parametrize("value", ["abc", "-1", "70000"])
def test_a_wrong_preview_port_stops_the_start(value: str) -> None:
    with pytest.raises(ConfigurationError):
        parse_port(value)


# --- The boundary of YAAP itself --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        ("http://localhost:8081", FOREIGN_ORIGIN),
        ("http://127.0.0.1:8081", FOREIGN_ORIGIN),
        ("http://[::1]:8081", FOREIGN_ORIGIN),
        ("http://localhost:8080", None),
        ("http://localhost", None),
        (None, None),
    ],
)
def test_the_boundary_refuses_the_preview_origin(origin: str | None, expected: object) -> None:
    assert refusal_for("localhost:8080", origin, ALLOWED, frozenset({8081})) == expected


# --- Through the listener ---------------------------------------------------------------------------------------------


@pytest.fixture
def preview_port(monkeypatch: pytest.MonkeyPatch) -> int:
    port = free_port()
    monkeypatch.setenv("YAAP_PREVIEW_PORT", str(port))
    return port


@pytest.fixture
def preview_client(preview_port: int, client: TestClient) -> TestClient:
    """YAAP with its preview listener on a free port, started by the app's lifespan as in production."""
    return client


def start_dev_server(client: TestClient, project_id: int, tmp_path: Path) -> ManagedProcess:
    manager: ProcessManager = client.app.state.processes
    port = free_port()
    start = functools.partial(
        manager.start,
        command=f'"{PYTHON}" "{DEV_SERVER}" {port}',
        executor=CONTAINER.executor(str(tmp_path)),
        env=command_environment(),
        environment_id=1,
        project_id=project_id,
        conversation_id=None,
        port=port,
    )
    managed = client.portal.call(start)
    for _ in range(100):
        if b"ready" in managed.buffer:
            return managed
        client.portal.call(functools.partial(asyncio.sleep, 0.1))
    raise AssertionError("The dev server did not start: " + managed.tail())


@pytest.fixture
def dev(preview_client: TestClient, tmp_path: Path) -> Iterator[tuple[TestClient, dict, ManagedProcess]]:
    project = preview_client.post("/api/v1/environments/1/projects", json={"name": "Site"}).json()
    managed = start_dev_server(preview_client, project["id"], tmp_path)
    yield preview_client, project, managed
    manager: ProcessManager = preview_client.app.state.processes
    for running in list(manager.processes.values()):
        preview_client.portal.call(manager.stop, running, "test")


def listener(port: int) -> httpx.Client:
    """The browser on the preview's address: Host names it as the browser would."""
    return httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"Host": f"testserver:{port}"}, timeout=20)


def open_link(client: TestClient, project_id: int, process_id: int) -> str:
    response = client.post(f"/api/v1/projects/{project_id}/processes/{process_id}/preview")
    assert response.status_code == 200, response.text
    return response.json()["url"]


def open_preview(client: TestClient, port: int, url: str) -> str:
    """Follow an open link; returns the preview cookie it set."""
    path = url.split(f":{port}", 1)[1]
    response = listener(port).get(path)
    assert response.status_code == 303, response.text
    assert response.headers["location"] == "/"
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    return cookie.split(";", 1)[0].split("=", 1)[1]


def test_a_request_through_the_proxy_reaches_the_dev_server_without_yaaps_cookie_or_authorization(dev, preview_port):
    client, project, managed = dev
    url = open_link(client, project["id"], managed.id)
    assert url.startswith(f"http://testserver:{preview_port}/__yaap_preview/open?process={managed.id}&token=")
    cookie = open_preview(client, preview_port, url)

    session = client.cookies["yaap_session"]
    response = listener(preview_port).post(
        "/api/items?x=1",
        content=b"name=cake",
        headers={
            "Cookie": f"yaap_session={session}; yaap_csrf=c; {COOKIE_NAME}={cookie}; theme=dark",
            "Authorization": "Bearer yaap-api-token",
            "Origin": f"http://testserver:{preview_port}",
        },
    )
    assert response.status_code == 200, response.text
    received = response.json()
    headers = {name.lower(): value for name, value in received["headers"]}
    assert received["path"] == "/api/items?x=1" and received["body"] == "name=cake"
    assert headers["cookie"] == "theme=dark"
    assert "authorization" not in headers
    assert session not in json.dumps(received)
    assert headers["host"] == f"localhost:{managed.port}"
    assert headers["origin"] == f"http://localhost:{managed.port}"
    # The dev server's own cookie reaches the browser; one named like YAAP's does not.
    assert response.headers.get_list("set-cookie") == ["app_cookie=1; Path=/"]


def test_requests_without_a_valid_preview_cookie_are_refused(dev, preview_port):
    client, project, managed = dev
    browser = listener(preview_port)
    assert browser.get("/").status_code == 403
    assert browser.get("/", headers={"Cookie": f"{COOKIE_NAME}=forged.value"}).status_code == 403
    # YAAP's own session is no way in either.
    assert browser.get("/", headers={"Cookie": f"yaap_session={client.cookies['yaap_session']}"}).status_code == 403
    # Another host name (DNS rebinding) is refused before anything else.
    assert browser.get("/", headers={"Host": f"evil.example:{preview_port}"}).status_code == 400


def test_an_open_link_works_once(dev, preview_port):
    client, project, managed = dev
    url = open_link(client, project["id"], managed.id)
    open_preview(client, preview_port, url)
    assert listener(preview_port).get(url.split(f":{preview_port}", 1)[1]).status_code == 403


def test_an_expired_token_is_refused(dev, preview_port):
    client, project, managed = dev
    preview: PreviewService = client.app.state.preview
    url = open_link(client, project["id"], managed.id)
    real_clock = preview.clock
    preview.clock = lambda: real_clock() + tokens.OPEN_SECONDS + 1
    try:
        assert listener(preview_port).get(url.split(f":{preview_port}", 1)[1]).status_code == 403
    finally:
        preview.clock = real_clock
    # A session that ran out is refused as well.
    cookie = open_preview(client, preview_port, open_link(client, project["id"], managed.id))
    preview.clock = lambda: real_clock() + tokens.SESSION_SECONDS + 1
    try:
        assert listener(preview_port).get("/", headers={"Cookie": f"{COOKIE_NAME}={cookie}"}).status_code == 403
    finally:
        preview.clock = real_clock


def test_a_token_for_one_process_does_not_open_another(dev, preview_port, tmp_path):
    client, project, first = dev
    second = start_dev_server(client, project["id"], tmp_path)
    url = open_link(client, project["id"], first.id)
    swapped = url.replace(f"process={first.id}", f"process={second.id}")
    assert listener(preview_port).get(swapped.split(f":{preview_port}", 1)[1]).status_code == 403

    cookie = open_preview(client, preview_port, url)
    reached = listener(preview_port).get("/", headers={"Cookie": f"{COOKIE_NAME}={cookie}"})
    assert reached.json()["port"] == first.port
    # Once the process ends, its cookie opens nothing, also not a process that takes over its port.
    client.portal.call(client.app.state.processes.stop, first, "test")
    assert listener(preview_port).get("/", headers={"Cookie": f"{COOKIE_NAME}={cookie}"}).status_code == 403
    # A link is only minted for a process of the project it is asked through.
    other = client.post("/api/v1/environments/1/projects", json={"name": "Other"}).json()
    assert client.post(f"/api/v1/projects/{other['id']}/processes/{second.id}/preview").status_code == 404


def test_a_page_on_the_preview_origin_cannot_change_anything_in_yaap(dev, preview_port):
    client, _, _ = dev
    body = {"name": "From the preview", "colour": "#123456"}
    for host in ("testserver", "localhost", "127.0.0.1"):
        origin = {"Origin": f"http://{host}:{preview_port}"}
        assert client.post("/api/v1/environments", json=body, headers=origin).status_code == 403, host
        assert client.put("/api/v1/settings/interface", json={"value": {}}, headers=origin).status_code == 403
    names = [item["name"] for item in client.get("/api/v1/environments").json()]
    assert "From the preview" not in names
    # YAAP's own page still works.
    assert client.post("/api/v1/environments", json=body, headers={"Origin": "http://testserver"}).status_code == 201


def test_a_websocket_is_relayed_for_hot_reload(dev, preview_port):
    client, project, managed = dev
    cookie = open_preview(client, preview_port, open_link(client, project["id"], managed.id))
    with socket.create_connection(("127.0.0.1", preview_port), timeout=20) as connection:
        connection.sendall(
            (
                "GET /@vite/client HTTP/1.1\r\n"
                f"Host: testserver:{preview_port}\r\n"
                f"Origin: http://testserver:{preview_port}\r\n"
                f"Cookie: {COOKIE_NAME}={cookie}\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\n"
                "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n"
            ).encode()
        )
        head = b""
        while b"\r\n\r\n" not in head:
            head += connection.recv(4096)
        assert head.startswith(b"HTTP/1.0 101") or head.startswith(b"HTTP/1.1 101"), head
        connection.sendall(b"reload")
        assert connection.recv(4096) == b"RELOAD"


def test_without_a_preview_port_no_link_is_made(client):
    project = client.post("/api/v1/environments/1/projects", json={"name": "Site"}).json()
    response = client.post(f"/api/v1/projects/{project['id']}/processes/1/preview")
    assert response.status_code == 409 and "switched off" in response.json()["detail"]
