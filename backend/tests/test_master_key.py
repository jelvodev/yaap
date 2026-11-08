"""The master key from a file, and rotation to a new key without downtime."""

import pytest
from cryptography.fernet import Fernet

from app.config import ConfigurationError, load_settings
from app.core.restore import decrypts
from app.credentials.crypto import CredentialCipher, CredentialDecryptionError
from app.storage.models import VaultEntry
from app.vault.rotation import check_keys, rotate_all


def test_the_key_can_come_from_a_file(tmp_path, monkeypatch):
    key = Fernet.generate_key()
    (tmp_path / "key").write_text(key.decode() + "\n", encoding="utf-8")
    monkeypatch.setenv("YAAP_MASTER_KEY", "")
    monkeypatch.setenv("YAAP_MASTER_KEY_FILE", str(tmp_path / "key"))
    settings = load_settings()
    assert settings.master_key == key and settings.master_key_source == "file"

    monkeypatch.setenv("YAAP_MASTER_KEY_FILE", str(tmp_path / "missing"))
    with pytest.raises(ConfigurationError, match="cannot be read"):
        load_settings()


def test_a_wrong_previous_key_stops_the_start(monkeypatch):
    monkeypatch.setenv("YAAP_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("YAAP_MASTER_KEY_FILE", raising=False)
    monkeypatch.setenv("YAAP_MASTER_KEY_PREVIOUS", "not-a-key")
    with pytest.raises(ConfigurationError, match="PREVIOUS"):
        load_settings()


def test_rotation_moves_every_value_to_the_new_key(client, master_key):
    client.post("/api/v1/vault/entries", json={"name": "ONE", "kind": "generic", "value": "first value"})
    client.post("/api/v1/vault/entries", json={"environment_id": 1, "name": "TWO", "kind": "generic", "value": "second"})
    old_key = master_key.encode()
    new_key = Fernet.generate_key()
    rotating = CredentialCipher(new_key, old_key)
    with client.app.state.session_factory() as session:
        tokens = [entry.encrypted_value for entry in session.query(VaultEntry).order_by(VaultEntry.id)]
        with pytest.raises(CredentialDecryptionError):
            CredentialCipher(new_key).decrypt(tokens[0])
        # During the rotation both keys read; the start re-encrypts everything with the new key.
        assert [rotating.decrypt(token) for token in tokens] == ["first value", "second"]
        assert check_keys(session, rotating).on_previous == 2
        state = rotate_all(session, rotating)
        assert state.rotated == 2 and state.unreadable == 0
        assert check_keys(session, rotating).on_previous == 0
        values = [CredentialCipher(new_key).decrypt(entry.encrypted_value) for entry in session.query(VaultEntry).order_by(VaultEntry.id)]
    assert values == ["first value", "second"]


def test_settings_say_when_every_value_uses_the_key(client):
    client.post("/api/v1/vault/entries", json={"name": "ONE", "kind": "generic", "value": "first value"})
    state = client.get("/api/v1/vault/master-key").json()
    assert state["source"] == "environment" and state["rotating"] is False
    assert state["unreadable"] == 0 and "uses the master key" in state["message"]


def test_the_restore_check_accepts_a_backup_made_with_the_previous_key(client, tmp_path, master_key):
    client.post("/api/v1/vault/entries", json={"name": "ONE", "kind": "generic", "value": "first value"})
    old_key = master_key.encode()
    new_key = Fernet.generate_key()
    database = tmp_path / "yaap.db"
    assert decrypts(database, CredentialCipher(new_key)) is False
    assert decrypts(database, CredentialCipher(new_key, old_key)) is True
