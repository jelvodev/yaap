import sqlite3

from tests.conftest import VALID_KEY

BASE = "/api/v1/environments/1/credentials"


def test_status_without_key(client):
    statuses = client.get(BASE).json()
    assert statuses == [
        {
            "kind": "anthropic_api_key",
            "label": "Anthropic API key",
            "description": "Test key.",
            "configured": False,
            "last_four": None,
            "updated_at": None,
            "last_tested_at": None,
            "last_test_result": None,
        }
    ]


def test_rejected_key_is_not_stored(client):
    response = client.put(f"{BASE}/anthropic_api_key", json={"value": "sk-test-wrong-key"})
    assert response.status_code == 422
    assert "not saved" in response.json()["detail"]
    assert client.get(BASE).json()[0]["configured"] is False


def test_valid_key_is_stored_encrypted_and_never_returned(client, tmp_path):
    response = client.put(f"{BASE}/anthropic_api_key", json={"value": VALID_KEY})
    assert response.status_code == 200
    status = response.json()
    assert status["configured"] is True
    assert status["last_four"] == VALID_KEY[-4:]
    assert VALID_KEY not in response.text
    assert VALID_KEY not in client.get(BASE).text

    with sqlite3.connect(tmp_path / "yaap.db") as connection:
        (stored,) = connection.execute("SELECT encrypted_value FROM vault_entries").fetchone()
    assert VALID_KEY.encode() not in stored


def test_keys_are_separate_per_environment(client):
    client.put(f"{BASE}/anthropic_api_key", json={"value": VALID_KEY})
    assert client.get("/api/v1/environments/2/credentials").json()[0]["configured"] is False


def test_stored_key_can_be_tested_and_removed(client):
    client.put(f"{BASE}/anthropic_api_key", json={"value": VALID_KEY})

    tested = client.post(f"{BASE}/anthropic_api_key/test")
    assert tested.json() == {"ok": True, "message": "The key works."}

    assert client.delete(f"{BASE}/anthropic_api_key").status_code == 204
    assert client.get(BASE).json()[0]["configured"] is False


def test_unknown_credential_type(client):
    assert client.put(f"{BASE}/unknown", json={"value": VALID_KEY}).status_code == 404
