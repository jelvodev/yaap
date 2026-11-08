"""The browser boundary: the host allowlist and the origin check in front of everything YAAP serves."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.routing import WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from app.auth.boundary import (
    BAD_HOST,
    FOREIGN_ORIGIN,
    BrowserBoundary,
    Refusal,
    allowed_hosts,
    host_allowed,
    origin_allowed,
    parse_allowed_hosts,
    refusal_for,
    request_refusal,
)
from app.config import ConfigurationError
from app.main import create_app

ALLOWED = allowed_hosts({"yaap.lan"})


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("localhost", True),
        ("localhost:8080", True),
        ("LOCALHOST:5173", True),
        ("127.0.0.1:8080", True),
        ("127.0.0.1", True),
        ("[::1]:8080", True),
        ("[::1]", True),
        ("yaap.lan:8080", True),
        ("evil.example", False),
        ("evil.example:8080", False),
        ("localhost.evil.example", False),
        ("127.0.0.1.nip.io", False),
        ("127.0.0.2:8080", False),
        ("0.0.0.0:8080", False),
        ("localhost:http", False),
        ("[::1", False),
        ("user@localhost", False),
        ("localhost/path", False),
        ("", False),
        (None, False),
    ],
)
def test_host_decision(host: str | None, expected: bool) -> None:
    assert host_allowed(host, ALLOWED) is expected


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        (None, True),
        ("http://localhost:8080", True),
        ("http://localhost:5173", True),
        ("http://127.0.0.1:8080", True),
        ("http://[::1]:8080", True),
        ("https://localhost", True),
        ("http://yaap.lan:8080", True),
        ("https://evil.example", False),
        ("http://localhost.evil.example", False),
        ("http://127.0.0.1.evil.example:8080", False),
        ("null", False),
        ("file://", False),
        ("chrome-extension://abcdef", False),
        ("ws://localhost:8080", False),
        ("http://localhost:8080/path", False),
        ("http://user@localhost:8080", False),
        ("http://[::1", False),
        ("", False),
    ],
)
def test_origin_decision(origin: str | None, expected: bool) -> None:
    assert origin_allowed(origin, ALLOWED) is expected


@pytest.mark.parametrize(
    ("host", "origin", "expected"),
    [
        ("127.0.0.1:8080", None, None),
        ("localhost:5173", "http://localhost:5173", None),
        ("evil.example", None, BAD_HOST),
        ("evil.example", "http://evil.example", BAD_HOST),
        ("localhost:8080", "http://evil.example", FOREIGN_ORIGIN),
    ],
)
def test_host_comes_before_origin(host: str, origin: str | None, expected: object) -> None:
    assert refusal_for(host, origin, ALLOWED) == expected


PAGE_FILE = "/api/v1/plugins/projects/page/t0k3n/app.js"


@pytest.mark.parametrize(
    ("method", "path", "host", "origin", "expected"),
    [
        # A sandboxed plugin page loads the SDK and its own ES modules in CORS mode, with Origin: null.
        ("GET", "/plugin-sdk.js", "localhost:8080", "null", None),
        ("HEAD", "/plugin-sdk.js", "localhost:8080", "null", None),
        ("GET", PAGE_FILE, "localhost:8080", "null", None),
        ("GET", "/api/v1/plugins/projects/page/t0k3n/", "localhost:8080", "null", None),
        # The Host allowlist applies to them as to everything.
        ("GET", "/plugin-sdk.js", "evil.example", "null", BAD_HOST),
        ("GET", PAGE_FILE, "evil.example", "null", BAD_HOST),
        # Nothing else comes from an opaque origin: not the API, not a change, not another file or door.
        ("GET", "/api/v1/environments", "localhost:8080", "null", FOREIGN_ORIGIN),
        ("PUT", "/api/v1/settings/interface", "localhost:8080", "null", FOREIGN_ORIGIN),
        ("OPTIONS", "/api/v1/environments", "localhost:8080", "null", FOREIGN_ORIGIN),
        ("OPTIONS", "/plugin-sdk.js", "localhost:8080", "null", FOREIGN_ORIGIN),
        ("POST", "/plugin-sdk.js", "localhost:8080", "null", FOREIGN_ORIGIN),
        ("POST", PAGE_FILE, "localhost:8080", "null", FOREIGN_ORIGIN),
        ("GET", "/api/v1/plugins", "localhost:8080", "null", FOREIGN_ORIGIN),
        ("GET", "/api/v1/plugins/projects/page", "localhost:8080", "null", FOREIGN_ORIGIN),
        ("GET", "/plugin-sdk.d.ts", "localhost:8080", "null", FOREIGN_ORIGIN),
        ("GET", "/plugin-sdk.js/x", "localhost:8080", "null", FOREIGN_ORIGIN),
        ("GET", "/", "localhost:8080", "null", FOREIGN_ORIGIN),
        ("POST", "/mcp", "localhost:8080", "null", FOREIGN_ORIGIN),
        ("POST", "/a2a", "localhost:8080", "null", FOREIGN_ORIGIN),
        # The static-file exception is for an opaque origin only; a foreign origin stays refused there too.
        ("GET", "/plugin-sdk.js", "localhost:8080", "https://evil.example", FOREIGN_ORIGIN),
        ("GET", PAGE_FILE, "localhost:8080", "https://evil.example", FOREIGN_ORIGIN),
        # Everything else keeps the plain decision.
        ("GET", "/api/v1/environments", "localhost:8080", None, None),
        ("POST", "/api/v1/environments", "localhost:8080", "http://localhost:8080", None),
    ],
)
def test_an_opaque_origin_reaches_only_a_plugin_pages_static_files(
    method: str, path: str, host: str, origin: str | None, expected: Refusal | None
) -> None:
    assert request_refusal(method, path, host, origin, ALLOWED) == expected


def test_an_opaque_origin_gets_the_sdk_but_not_the_api(client: TestClient) -> None:
    frame = {"Origin": "null", "Sec-Fetch-Site": "cross-site"}
    # Without a frontend build the SDK is missing, but the request got past the boundary.
    assert client.get("/plugin-sdk.js", headers=frame).status_code == 404
    assert client.get("/api/v1/environments", headers=frame).status_code == 403
    assert client.put("/api/v1/settings/interface", json={"value": {}}, headers=frame).status_code == 403


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", frozenset()),
        ("yaap.lan", frozenset({"yaap.lan"})),
        ("yaap.lan, Desk.Local", frozenset({"yaap.lan", "desk.local"})),
        ("yaap.lan desk.local:8080", frozenset({"yaap.lan", "desk.local"})),
        ("[fe80::1]", frozenset({"fe80::1"})),
    ],
)
def test_allowed_hosts_are_read_from_the_setting(value: str, expected: frozenset[str]) -> None:
    assert parse_allowed_hosts(value) == expected


@pytest.mark.parametrize("value", ["http://yaap.lan", "yaap.lan/x", "user@yaap.lan", "yaap.lan:port"])
def test_a_malformed_allowed_host_stops_the_start(value: str) -> None:
    with pytest.raises(ConfigurationError, match="YAAP_ALLOWED_HOSTS"):
        parse_allowed_hosts(value)


@pytest.mark.parametrize(
    "path", ["/api/v1/health", "/api/v1/environments/1/conversations", "/mcp", "/a2a", "/mcp/outside", "/api/v1/plugins/x/page/t/"]
)
def test_a_foreign_host_gets_400_everywhere(client: TestClient, path: str) -> None:
    response = client.get(path, headers={"Host": "evil.example"})
    assert response.status_code == 400
    assert "YAAP_ALLOWED_HOSTS" in response.json()["detail"]


def test_a_foreign_host_cannot_read_the_conversation_list(client: TestClient) -> None:
    assert client.get("/api/v1/environments/1/conversations").status_code == 200
    assert client.get("/api/v1/environments/1/conversations", headers={"Host": "evil.example"}).status_code == 400


def test_a_post_with_a_foreign_origin_gets_403(client: TestClient) -> None:
    body = {"name": "Foreign", "colour": "#123456"}
    response = client.post("/api/v1/environments", json=body, headers={"Origin": "http://evil.example"})
    assert response.status_code == 403
    assert client.post("/mcp", json={}, headers={"Origin": "http://evil.example"}).status_code == 403
    assert client.post("/a2a", json={}, headers={"Origin": "null"}).status_code == 403
    assert client.post("/api/v1/environments", json=body, headers={"Origin": "http://testserver"}).status_code == 201


def test_the_vite_proxy_and_the_internal_mcp_address_pass(client: TestClient) -> None:
    # Vite proxies /api with the browser's own Host and Origin.
    vite = {"Host": "localhost:5173", "Origin": "http://localhost:5173"}
    assert client.get("/api/v1/health", headers=vite).status_code == 200
    # Claude Code and Codex call http://127.0.0.1:8080/mcp without an Origin: refused for the token, not the address.
    assert client.post("/mcp", json={}, headers={"Host": "127.0.0.1:8080"}).status_code == 401


def test_a_name_in_yaap_allowed_hosts_is_answered(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("YAAP_ALLOWED_HOSTS", "yaap.lan")
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>YAAP</html>", encoding="utf-8")
    frontend = TestClient(create_app(static_dir=tmp_path), base_url="http://yaap.lan:8080")
    assert frontend.get("/").status_code == 200
    # The built frontend is behind the boundary as well.
    assert frontend.get("/", headers={"Host": "evil.example"}).status_code == 400
    assert frontend.get("/", headers={"Host": "testserver"}).status_code == 400


async def echo(websocket: WebSocket) -> None:
    await websocket.accept()
    await websocket.send_text("open")
    await websocket.close()


def test_a_websocket_from_a_foreign_origin_is_closed() -> None:
    app = BrowserBoundary(Starlette(routes=[WebSocketRoute("/socket", echo)]), allowed_hosts({"testserver"}))
    client = TestClient(app)
    with client.websocket_connect("/socket", headers={"Origin": "http://testserver"}) as socket:
        assert socket.receive_text() == "open"
    with pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect("/socket", headers={"Origin": "https://evil.example"}):
            pass
    assert refused.value.code == 1008
