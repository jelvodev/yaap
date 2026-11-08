"""Restore from backup: checks, upload, the staged restore and putting it in place on start."""

import gc
import io
import json
import zipfile

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.core.restore import PENDING, PREVIOUS, STAGING, check_backup, safe_member
from app.credentials.crypto import CredentialCipher
from app.main import create_app
from app.storage.db import known_revision
from tests.conftest import CHEAP_HASHER, sign_in
from tests.test_chat import add_key


def zip_with(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return output.getvalue()


def rewrite_manifest(data: bytes, **changes) -> bytes:
    source = zipfile.ZipFile(io.BytesIO(data))
    files = {name: source.read(name) for name in source.namelist()}
    manifest = json.loads(files["manifest.json"])
    manifest.update(changes)
    files["manifest.json"] = json.dumps(manifest).encode()
    return zip_with(files)


def test_only_yaap_data_may_be_in_a_backup():
    assert safe_member("yaap.db") and safe_member("workspace/1/site/index.html") and safe_member("knowledge/1/2/text.md")
    assert not safe_member("../etc/passwd") and not safe_member("/etc/passwd") and not safe_member("other/file")
    assert not safe_member("workspace/../../x")


def test_a_backup_is_checked_and_uploaded(client, tmp_path):
    add_key(client)
    made = client.post("/api/v1/backups").json()
    check = client.get(f"/api/v1/backups/{made['name']}/restore-check").json()
    assert check["restorable"] and check["problems"] == [] and check["database_revision"]

    data = client.get(f"/api/v1/backups/{made['name']}").content
    # The same backup again: it is already here.
    assert client.post("/api/v1/backups/upload", files={"file": ("copy.zip", data)}).status_code == 409
    older = rewrite_manifest(data, created_at="2026-01-02T03:04:05+00:00")
    uploaded = client.post("/api/v1/backups/upload", files={"file": ("old.zip", older)})
    assert uploaded.status_code == 201 and uploaded.json()["name"] == "yaap-backup-20260102-030405.zip"
    not_a_backup = client.post("/api/v1/backups/upload", files={"file": ("x.zip", zip_with({"a.txt": b"hi"}))})
    assert not_a_backup.status_code == 422 and "not a YAAP backup" in not_a_backup.json()["detail"]
    assert client.post("/api/v1/backups/upload", files={"file": ("x.zip", b"no zip")}).status_code == 422

    # A newer YAAP's backup, and one made with another master key, cannot be restored.
    newer = rewrite_manifest(data, database_revision="9999_from_the_future", created_at="2026-01-03T00:00:00+00:00")
    name = client.post("/api/v1/backups/upload", files={"file": ("new.zip", newer)}).json()["name"]
    problems = client.get(f"/api/v1/backups/{name}/restore-check").json()["problems"]
    assert any("newer YAAP" in problem for problem in problems)
    other_key = CredentialCipher(Fernet.generate_key())
    path = tmp_path / "backups" / made["name"]
    assert any("another master key" in problem for problem in check_backup(path, other_key, known_revision).problems)
    refused = client.post(f"/api/v1/backups/{name}/restore", json={"confirm": name})
    assert refused.status_code == 409 and "newer YAAP" in refused.json()["detail"]


def test_a_restore_is_staged_then_put_in_place_on_the_next_start(client, tmp_path, monkeypatch, master_key):
    restarts = []
    client.app.state.request_restart = lambda: restarts.append(True)
    add_key(client)
    before = client.post("/api/v1/environments/1/projects", json={"name": "Kept"}).json()
    made = client.post("/api/v1/backups").json()
    after = client.post("/api/v1/environments/1/projects", json={"name": "Made later"}).json()
    assert (tmp_path / "workspace").is_dir()

    wrong = client.post(f"/api/v1/backups/{made['name']}/restore", json={"confirm": "yes"})
    assert wrong.status_code == 422 and not restarts
    restored = client.post(f"/api/v1/backups/{made['name']}/restore", json={"confirm": made["name"]})
    assert restored.status_code == 200, restored.text
    safety = restored.json()["safety_backup"]
    assert restarts == [True]
    assert (tmp_path / PENDING).is_file() and (tmp_path / STAGING / "yaap.db").is_file()
    assert safety in {backup["name"] for backup in client.get("/api/v1/backups").json()}

    # YAAP stops; on the next start the backup goes in place before the database opens.
    client.__exit__(None, None, None)
    gc.collect()  # Windows keeps a file open while any connection to it lives.
    # The first start took the master key out of the environment; Docker gives it again at every start.
    monkeypatch.setenv("YAAP_MASTER_KEY", master_key)
    second = create_app(credential_types=client.app.state.credential_types, providers=client.app.state.providers)
    second.state.load_embedder = lambda: None
    second.state.background_indexing = False
    second.state.password_hasher = CHEAP_HASHER
    with TestClient(second) as again:
        # The backup holds the account of before, with the same password.
        sign_in(again)
        names = {project["name"] for project in again.get("/api/v1/environments/1/projects").json()}
        assert names == {before["name"]}
        assert after["name"] not in names
        # The key still works: the same master key.
        credentials = again.get("/api/v1/environments/1/credentials").json()
        assert any(item["kind"] == "anthropic_api_key" and item["configured"] for item in credentials)
    assert not (tmp_path / PENDING).exists() and not (tmp_path / STAGING).exists()
    assert (tmp_path / PREVIOUS / "yaap.db").is_file()
