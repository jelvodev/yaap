"""Authentication: the account, browser sessions with CSRF, API tokens, and the gate on every route."""

import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient

from app.api.plugins import SDK_DECLARATIONS, SDK_SCRIPT
from app.auth.gate import OPEN_PATHS, plugin_page, token_path
from app.auth.tokens import TOKEN_PATHS, TokenGrant, issue_api_token, may_modify_vault_entry, require_api_token
from app.storage.models import Account, AuthSession, VaultEntry
from app.version import VERSION
from tests.conftest import TEST_PASSWORD, sign_in


def anonymous(client: TestClient) -> TestClient:
    """A client of the same app without the fixture's session (the app is already started)."""
    return TestClient(client.app)


def concrete(path: str) -> str:
    path = re.sub(r"\{[^}]+:path\}", "some/file.txt", path)
    return re.sub(r"\{[^}]+\}", "1", path)


def test_every_api_route_refuses_without_a_session_or_token(client: TestClient) -> None:
    outsider = anonymous(client)
    walked: set[tuple[str, str]] = set()
    # Every registered route, also those of included routers and those left out of the OpenAPI description (/mcp).
    for route in iter_route_contexts(client.app.routes):
        if not isinstance(route.original_route, APIRoute) or route.path in OPEN_PATHS:
            continue
        if route.path in (SDK_SCRIPT, SDK_DECLARATIONS):
            # The plugin SDK is part of the built frontend, outside /api, and holds no data.
            continue
        for method in sorted((route.methods or set()) - {"HEAD", "OPTIONS"}):
            response = outsider.request(method, concrete(route.path), json={})
            if plugin_page(concrete(route.path), method):
                # A plugin page checks its own page token: an unknown one finds nothing.
                assert response.status_code == 404, f"{method} {route.path} answered {response.status_code}"
                walked.add((method, route.path))
                continue
            assert response.status_code == 401, f"{method} {route.path} answered {response.status_code}"
            walked.add((method, route.path))
    described = {
        (method.upper(), path)
        for path, operations in client.app.openapi()["paths"].items()
        for method in operations
        if path not in OPEN_PATHS
    }
    assert described <= walked and len(walked) > 200
    assert {("POST", "/mcp"), ("GET", "/mcp")} <= walked
    # What stays open: the sign-in state, signing in, the first-run password and the health check.
    assert outsider.get("/api/v1/health").json() == {"status": "ok"}
    assert outsider.get("/api/v1/auth/state").json() == {"password_set": True, "signed_in": False, "method": None}


@pytest.mark.parametrize(
    ("commit", "channel"),
    [(None, None), ("abc1234", "stable")],
)
def test_version_is_open_without_signing_in(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, commit: str | None, channel: str | None
) -> None:
    for name, value in (("YAAP_COMMIT", commit), ("YAAP_CHANNEL", channel)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    answer = anonymous(client).get("/api/v1/version")
    assert answer.status_code == 200
    assert answer.json() == {"version": VERSION, "commit": commit or "", "channel": channel or ""}


def test_token_paths_are_left_to_their_routes() -> None:
    for prefix in ("/.well-known/agent-card.json", "/a2a", "/mcp/outside", "/mcp"):
        assert prefix in TOKEN_PATHS
        assert token_path(prefix)
    assert token_path("/a2a/tasks") and not token_path("/a2ab") and not token_path("/api/v1/environments")


def test_first_run_sets_the_password_once(tmp_path, monkeypatch) -> None:
    from cryptography.fernet import Fernet

    from app.main import create_app
    from tests.conftest import CHEAP_HASHER

    monkeypatch.setenv("YAAP_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("YAAP_DATA_DIR", str(tmp_path))
    app = create_app()
    app.state.password_hasher = CHEAP_HASHER
    app.state.scheduler = False
    app.state.background_indexing = False
    app.state.load_embedder = lambda: None
    with TestClient(app) as browser:
        assert browser.get("/api/v1/auth/state").json() == {"password_set": False, "signed_in": False, "method": None}
        refused = browser.get("/api/v1/environments")
        assert refused.status_code == 401 and refused.json()["detail"] == "Set a password for YAAP first."
        assert browser.post("/api/v1/auth/sign-in", json={"password": "anything-long"}).status_code == 409
        assert browser.post("/api/v1/auth/setup", json={"password": "short"}).status_code == 422
        response = browser.post("/api/v1/auth/setup", json={"password": "first password"})
        assert response.status_code == 200 and response.json()["signed_in"] is True
        assert browser.get("/api/v1/environments").status_code == 200
        # Once set, the first-run screen is gone: nobody else can set it.
        assert anonymous(browser).post("/api/v1/auth/setup", json={"password": "another password"}).status_code == 409
        with app.state.session_factory() as session:
            assert session.get(Account, 1).password_hash.startswith("$argon2id$")


def test_session_cookie_is_httponly_and_strict(client: TestClient) -> None:
    browser = anonymous(client)
    response = browser.post("/api/v1/auth/sign-in", json={"password": TEST_PASSWORD})
    cookies = response.headers.get_list("set-cookie")
    session_cookie = next(cookie for cookie in cookies if cookie.startswith("yaap_session="))
    csrf_cookie = next(cookie for cookie in cookies if cookie.startswith("yaap_csrf="))
    assert "HttpOnly" in session_cookie and "SameSite=strict" in session_cookie
    assert "HttpOnly" not in csrf_cookie and "SameSite=strict" in csrf_cookie
    # Only hashes are stored.
    token = browser.cookies["yaap_session"]
    with client.app.state.session_factory() as session:
        assert all(token not in (record.id, record.csrf_hash) for record in session.query(AuthSession))


def test_cookie_names_follow_the_port(client: TestClient) -> None:
    browser = TestClient(client.app, base_url="http://127.0.0.1:8091")
    browser.post("/api/v1/auth/sign-in", json={"password": TEST_PASSWORD})
    assert "yaap_session_8091" in browser.cookies and "yaap_session" not in browser.cookies
    assert browser.get("/api/v1/environments").status_code == 200


def test_wrong_password_and_sign_out(client: TestClient) -> None:
    browser = anonymous(client)
    assert browser.post("/api/v1/auth/sign-in", json={"password": "wrong password"}).status_code == 401
    sign_in(browser)
    assert browser.get("/api/v1/environments").status_code == 200
    assert browser.post("/api/v1/auth/sign-out").status_code == 204
    assert browser.get("/api/v1/environments").status_code == 401


def test_too_many_wrong_passwords_wait(client: TestClient, monkeypatch) -> None:
    from app.api import auth

    monkeypatch.setattr(auth, "failures", auth.deque())
    browser = anonymous(client)
    for _ in range(auth.MAX_FAILURES):
        assert browser.post("/api/v1/auth/sign-in", json={"password": "wrong password"}).status_code == 401
    assert browser.post("/api/v1/auth/sign-in", json={"password": TEST_PASSWORD}).status_code == 429


def test_changes_need_the_csrf_token_and_an_allowed_origin(client: TestClient) -> None:
    body = {"name": "Csrf", "colour": "#123456"}
    csrf = client.headers.pop("X-CSRF-Token")
    assert client.post("/api/v1/environments", json=body).status_code == 403
    assert client.post("/api/v1/environments", json=body, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    other_site = {"X-CSRF-Token": csrf, "Origin": "http://evil.example"}
    assert client.post("/api/v1/environments", json=body, headers=other_site).status_code == 403
    same = {"X-CSRF-Token": csrf, "Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}
    assert client.post("/api/v1/environments", json=body, headers=same).status_code == 201
    # Reading needs no CSRF token.
    assert client.get("/api/v1/environments").status_code == 200


def test_password_change_signs_other_browsers_out(client: TestClient) -> None:
    other = anonymous(client)
    sign_in(other)
    wrong = client.put("/api/v1/auth/password", json={"current_password": "nope", "new_password": "new password 1"})
    assert wrong.status_code == 422
    response = client.put("/api/v1/auth/password", json={"current_password": TEST_PASSWORD, "new_password": "new password 1"})
    assert response.status_code == 204
    assert client.get("/api/v1/environments").status_code == 200
    assert other.get("/api/v1/environments").status_code == 401
    fresh = anonymous(client)
    assert fresh.post("/api/v1/auth/sign-in", json={"password": TEST_PASSWORD}).status_code == 401
    assert fresh.post("/api/v1/auth/sign-in", json={"password": "new password 1"}).status_code == 200


def test_api_token_is_shown_once_works_and_is_revoked(client: TestClient) -> None:
    created = client.post("/api/v1/auth/tokens", json={"name": "script", "purpose": "api"})
    assert created.status_code == 201
    token = created.json()["token"]
    assert token.startswith("yaap_")
    listed = client.get("/api/v1/auth/tokens").json()
    assert [item["name"] for item in listed] == ["script"] and "token" not in listed[0]
    assert listed[0]["scope_name"] == "Everything"
    # The vault holds it encrypted, with only a hash to check it.
    with client.app.state.session_factory() as session:
        entry = session.query(VaultEntry).filter_by(kind="api_token").one()
        assert token.encode() not in entry.encrypted_value and token not in str(entry.public_fields)
    script = anonymous(client)
    headers = {"Authorization": f"Bearer {token}"}
    assert script.get("/api/v1/environments", headers=headers).status_code == 200
    # No CSRF for a token: no browser sends it by itself.
    assert script.post("/api/v1/environments", json={"name": "By token", "colour": "#654321"}, headers=headers).status_code == 201
    # A token cannot manage the account or make more tokens.
    assert script.get("/api/v1/auth/tokens", headers=headers).status_code == 403
    assert script.post("/api/v1/auth/tokens", json={"name": "more", "purpose": "api"}, headers=headers).status_code == 403
    assert client.delete(f"/api/v1/auth/tokens/{listed[0]['id']}").status_code == 204
    refused = script.get("/api/v1/environments", headers=headers)
    assert refused.status_code == 401 and "not valid" in refused.json()["detail"]


def test_api_token_cannot_manage_tokens_through_the_vault(client: TestClient) -> None:
    created = client.post("/api/v1/auth/tokens", json={"name": "script", "purpose": "api"}).json()
    entry_id = created["id"]
    headers = {"Authorization": f"Bearer {created['token']}"}
    script = anonymous(client)
    path = f"/api/v1/vault/entries/{entry_id}"
    assert script.patch(path, json={"description": "changed"}, headers=headers).status_code == 403
    assert script.delete(path, headers=headers).status_code == 403
    assert script.get("/api/v1/environments", headers=headers).status_code == 200
    assert client.patch(path, json={"description": "changed"}).status_code == 200
    assert client.delete(path).status_code == 204
    assert script.get("/api/v1/environments", headers=headers).status_code == 401


@pytest.mark.parametrize(
    ("method", "kind", "allowed"),
    [("session", "api_token", True), ("token", "api_token", False), (None, "api_token", False),
     ("token", "generic", True), ("session", "generic", True)],
)
def test_vault_token_management_requires_a_session(method: str | None, kind: str, allowed: bool) -> None:
    assert may_modify_vault_entry(method, kind) is allowed


def test_confined_tokens_need_an_environment_and_stay_off_the_rest_api(client: TestClient) -> None:
    assert client.post("/api/v1/auth/tokens", json={"name": "agent", "purpose": "a2a"}).status_code == 422
    project = client.post("/api/v1/environments/1/projects", json={"name": "Site"}).json()
    created = client.post(
        "/api/v1/auth/tokens", json={"name": "agent", "purpose": "a2a", "environment_id": 1, "project_id": project["id"]}
    ).json()
    assert created["scope_name"].endswith("› Site")
    refused = anonymous(client).get("/api/v1/environments", headers={"Authorization": f"Bearer {created['token']}"})
    assert refused.status_code == 403
    duplicate = client.post("/api/v1/auth/tokens", json={"name": "agent", "purpose": "mcp", "environment_id": 1})
    assert duplicate.status_code == 422
    # The vault lists it and refuses to replace its value; removing it there revokes it.
    entries = client.get("/api/v1/vault/entries", params={"level": "global"}).json()
    entry = next(item for item in entries if item["kind"] == "api_token")
    assert client.patch(f"/api/v1/vault/entries/{entry['id']}", json={"value": "x" * 30}).status_code == 409


def test_require_api_token_checks_purpose_and_confinement(client: TestClient) -> None:
    with client.app.state.session_factory() as session:
        token = issue_api_token(session, client.app.state.cipher, name="mcp client", purpose="mcp", environment_id=1)
        other = issue_api_token(session, client.app.state.cipher, name="a2a client", purpose="a2a", environment_id=1)
        expiring = issue_api_token(session, client.app.state.cipher, name="old", purpose="mcp", environment_id=1)
        entry = session.query(VaultEntry).filter_by(name="old").one()
        entry.expires_at = datetime.now(UTC) - timedelta(days=1)
        session.commit()

    probe = FastAPI()
    probe.state.session_factory = client.app.state.session_factory

    @probe.get("/probe")
    def read(grant: TokenGrant = Depends(require_api_token("mcp"))) -> dict:
        return {"purpose": grant.purpose, "environment_id": grant.environment_id, "project_id": grant.project_id}

    with TestClient(probe) as outsider:
        assert outsider.get("/probe").status_code == 401
        assert outsider.get("/probe", headers={"Authorization": "Bearer nonsense"}).status_code == 401
        assert outsider.get("/probe", headers={"Authorization": f"Bearer {other}"}).status_code == 401
        assert outsider.get("/probe", headers={"Authorization": f"Bearer {expiring}"}).status_code == 401
        good = outsider.get("/probe", headers={"Authorization": f"Bearer {token}"})
        assert good.status_code == 200 and good.json() == {"purpose": "mcp", "environment_id": 1, "project_id": None}


@pytest.mark.parametrize("path", ["/mcp"])
def test_mcp_endpoint_refuses_without_a_run_token(client: TestClient, path: str) -> None:
    outsider = anonymous(client)
    assert outsider.post(path, json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 401
    assert outsider.get(path).status_code == 401


def test_set_password_script_sets_resets_and_signs_out(client: TestClient, monkeypatch, capsys, master_key) -> None:
    import io

    from app.scripts import set_password as script

    monkeypatch.setattr(script, "default_hasher", lambda: client.app.state.password_hasher)
    monkeypatch.setattr("sys.stdin", io.StringIO("replaced password\n"))
    # An existing password stays unless --reset is given.
    monkeypatch.setenv("YAAP_MASTER_KEY", master_key)  # the script takes it out of the environment, as YAAP does
    assert script.main(["--stdin", "--if-unset"]) == 0
    assert "already has a password" in capsys.readouterr().out
    monkeypatch.setenv("YAAP_MASTER_KEY", master_key)  # the script takes it out of the environment, as YAAP does
    assert script.main(["--stdin", "--reset"]) == 0
    assert client.get("/api/v1/environments").status_code == 401
    browser = anonymous(client)
    assert browser.post("/api/v1/auth/sign-in", json={"password": "replaced password"}).status_code == 200
    monkeypatch.setattr("sys.stdin", io.StringIO("short\n"))
    monkeypatch.setenv("YAAP_MASTER_KEY", master_key)  # the script takes it out of the environment, as YAAP does
    assert script.main(["--stdin", "--reset"]) == 2
