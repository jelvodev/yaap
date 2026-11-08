"""The vault: one store for keys and secrets, several keys per kind, use log and expiry."""

import sqlite3
from datetime import UTC, datetime, timedelta

from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet

from app.credentials.crypto import CredentialCipher
from app.credentials.types import CredentialTestResult, CredentialType
from app.providers.fake_provider import ScriptedResponse
from app.secrets.service import resolve_secrets
from app.storage.db import MIGRATIONS_DIR
from tests.conftest import VALID_KEY
from tests.test_chat import add_key, send, start_conversation

OTHER_KEY = "sk-test-valid-key-9876"


def accept_both_keys(client) -> None:
    async def test(value: str) -> CredentialTestResult:
        return CredentialTestResult(value in (VALID_KEY, OTHER_KEY), "Checked.")

    types = client.app.state.credential_types
    types["anthropic_api_key"] = CredentialType(**{**types["anthropic_api_key"].__dict__, "test": test})


def migrate(url: str, revision: str) -> None:
    config = Config()
    config.set_main_option("script_location", MIGRATIONS_DIR.as_posix())
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, revision)


def test_the_migration_keeps_every_credential_and_secret(tmp_path):
    database = tmp_path / "old.db"
    url = f"sqlite:///{database.as_posix()}"
    migrate(url, "0026_conversation_access")
    cipher = CredentialCipher(Fernet.generate_key())
    moment = "2026-11-01 10:00:00.000000"
    with sqlite3.connect(database) as connection:
        if connection.execute("SELECT COUNT(*) FROM environments WHERE id = 1").fetchone()[0] == 0:
            connection.execute(
                "INSERT INTO environments (id, name, slug, colour, created_at) VALUES (1, 'Work', 'work', '#000000', ?)",
                (moment,),
            )
        connection.execute(
            "INSERT INTO credentials (environment_id, kind, encrypted_value, last_four, created_at, updated_at) "
            "VALUES (1, 'github_token', ?, 'abcd', ?, ?)",
            (cipher.encrypt("ghp_token_abcd"), moment, moment),
        )
        connection.execute(
            "INSERT INTO secrets (environment_id, project_id, name, encrypted_value, created_at, updated_at) "
            "VALUES (1, NULL, 'DB_PASSWORD', ?, ?, ?)",
            (cipher.encrypt("hunter22"), moment, moment),
        )
    migrate(url, "head")
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT environment_id, name, kind, agent_use, is_default, last_four, encrypted_value FROM vault_entries ORDER BY kind"
        ).fetchall()
    assert [row[:6] for row in rows] == [(1, "DB_PASSWORD", "generic", 1, 0, ""), (1, "default", "github_token", 0, 1, "abcd")]
    assert cipher.decrypt(rows[0][6]) == "hunter22" and cipher.decrypt(rows[1][6]) == "ghp_token_abcd"


def test_a_second_key_of_a_kind_becomes_default_only_when_chosen(client, fake):
    accept_both_keys(client)
    add_key(client)
    fake.responses.extend([ScriptedResponse(text="1"), ScriptedResponse(text="2")])
    created = client.post(
        "/api/v1/vault/entries",
        json={"environment_id": 1, "name": "client-org", "kind": "anthropic_api_key", "value": OTHER_KEY},
    )
    assert created.status_code == 201 and created.json()["is_default"] is False
    conversation_id = start_conversation(client)
    send(client, conversation_id, "One")
    assert fake.keys_used[-1] == VALID_KEY

    client.patch(f"/api/v1/vault/entries/{created.json()['id']}", json={"make_default": True})
    send(client, conversation_id, "Two")
    assert fake.keys_used[-1] == OTHER_KEY
    entries = client.get("/api/v1/vault/entries", params={"environment_id": 1}).json()
    assert [(entry["name"], entry["is_default"]) for entry in entries] == [("client-org", True), ("default", False)]
    assert OTHER_KEY not in str(entries) and VALID_KEY not in str(entries)


def test_a_failing_key_is_not_stored(client):
    response = client.post(
        "/api/v1/vault/entries", json={"environment_id": 1, "name": "bad", "kind": "anthropic_api_key", "value": "sk-wrong"}
    )
    assert response.status_code == 422
    assert client.get("/api/v1/vault/entries", params={"environment_id": 1}).json() == []


def test_keys_are_never_offered_to_the_agent_and_need_an_environment(client):
    assert client.post(
        "/api/v1/vault/entries", json={"name": "default", "kind": "anthropic_api_key", "value": VALID_KEY}
    ).status_code == 422
    assert client.post(
        "/api/v1/vault/entries", json={"environment_id": 1, "name": "lower", "kind": "generic", "value": "x"}
    ).status_code == 422


def test_secrets_resolve_from_global_to_project(client):
    project = client.post("/api/v1/environments/1/projects", json={"name": "Site"}).json()
    for level, value in ((None, "global-value"), (1, "environment-value")):
        client.post("/api/v1/vault/entries", json={"environment_id": level, "name": "TOKEN", "kind": "generic", "value": value})
    client.post("/api/v1/vault/entries", json={"name": "ONLY_GLOBAL", "kind": "generic", "value": "g-only"})
    client.post(
        "/api/v1/vault/entries",
        json={"environment_id": 1, "project_id": project["id"], "name": "TOKEN", "kind": "generic", "value": "project-value"},
    )
    app = client.app
    with app.state.session_factory() as session:
        assert resolve_secrets(session, app.state.cipher, 1, None) == {"ONLY_GLOBAL": "g-only", "TOKEN": "environment-value"}
        assert resolve_secrets(session, app.state.cipher, 1, project["id"])["TOKEN"] == "project-value"
        assert resolve_secrets(session, app.state.cipher, 2, None) == {"ONLY_GLOBAL": "g-only", "TOKEN": "global-value"}
    listed = client.get("/api/v1/vault/entries", params={"environment_id": 1, "project_id": project["id"]}).json()
    hidden = {(entry["level"], entry["name"]): entry["hidden_by_nearer"] for entry in listed}
    assert hidden == {
        ("project", "TOKEN"): False,
        ("environment", "TOKEN"): True,
        ("global", "TOKEN"): True,
        ("global", "ONLY_GLOBAL"): False,
    }
    # The old secrets API still works on the same store.
    names = [item["name"] for item in client.get("/api/v1/environments/1/secrets").json()]
    assert names == ["ONLY_GLOBAL", "TOKEN"]


def test_every_use_is_logged_without_the_value(client, fake):
    add_key(client)
    fake.responses.append(ScriptedResponse(text="Hi"))
    conversation_id = start_conversation(client)
    send(client, conversation_id, "Hello")
    entry = client.get("/api/v1/vault/entries", params={"environment_id": 1}).json()[0]
    detail = client.get(f"/api/v1/vault/entries/{entry['id']}").json()
    assert detail["entry"]["last_used_at"] is not None
    assert detail["uses"][0]["used_by"].endswith("reply") and detail["uses"][0]["conversation_id"] == conversation_id
    assert any("default" in place.lower() for place in detail["used_in"])
    assert VALID_KEY not in str(detail)


def test_expiry_shows_two_weeks_ahead(client):
    soon = (datetime.now(UTC) + timedelta(days=5)).isoformat()
    later = (datetime.now(UTC) + timedelta(days=60)).isoformat()
    first = client.post("/api/v1/vault/entries", json={"name": "SOON", "kind": "generic", "value": "v1", "expires_at": soon}).json()
    client.post("/api/v1/vault/entries", json={"name": "LATER", "kind": "generic", "value": "v2", "expires_at": later})
    assert first["expiry"] == "soon"
    assert [entry["name"] for entry in client.get("/api/v1/vault/attention").json()] == ["SOON"]
    client.patch(f"/api/v1/vault/entries/{first['id']}", json={"clear_expiry": True})
    assert client.get("/api/v1/vault/attention").json() == []


def test_deleting_the_default_promotes_the_next(client):
    accept_both_keys(client)
    add_key(client)
    other = client.post(
        "/api/v1/vault/entries",
        json={"environment_id": 1, "name": "second", "kind": "anthropic_api_key", "value": OTHER_KEY},
    ).json()
    default = next(e for e in client.get("/api/v1/vault/entries", params={"environment_id": 1}).json() if e["is_default"])
    assert client.delete(f"/api/v1/vault/entries/{default['id']}").status_code == 204
    left = client.get("/api/v1/vault/entries", params={"environment_id": 1}).json()
    assert [(entry["id"], entry["is_default"]) for entry in left] == [(other["id"], True)]
