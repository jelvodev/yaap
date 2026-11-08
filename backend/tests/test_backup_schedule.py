"""Backups on a schedule, copied to a folder or a network share."""

import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.backups import smb
from app.backups.runs import backup_tick, due_trigger
from app.core.restore import PENDING
from app.storage.models import BackupRun


@pytest.fixture
def backup_root(tmp_path, monkeypatch):
    root = tmp_path.parent / f"{tmp_path.name}-backup-root"
    root.mkdir()
    monkeypatch.setenv("YAAP_BACKUP_ROOT", str(root))
    return root


def set_destinations(client, destinations):
    return client.put("/api/v1/settings/backup_destinations", json={"value": destinations})


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_schedule_is_on_by_default_and_validated(client):
    settings = {item["key"]: item for item in client.get("/api/v1/settings").json()}
    assert settings["backup_schedule"]["value"] == {"enabled": True, "cron": "0 3 * * *", "keep": 7}
    assert client.put("/api/v1/settings/backup_schedule", json={"value": {"cron": "61 * * * *"}}).status_code == 422
    assert client.put("/api/v1/settings/backup_schedule", json={"value": {"keep": 0}}).status_code == 422
    assert client.put("/api/v1/settings/backup_schedule", json={"value": {"enabled": False}}).status_code == 200


def test_due_at_the_scheduled_time_and_a_missed_one_runs_once(client):
    client.put("/api/v1/settings/timezone", json={"value": "UTC"})
    since = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)
    with client.app.state.session_factory() as session:
        assert due_trigger(session, since, datetime(2026, 11, 8, 2, 59, tzinfo=UTC)) is None
        assert due_trigger(session, since, datetime(2026, 11, 8, 3, 0, 30, tzinfo=UTC)) == "schedule"
        assert due_trigger(session, since, datetime(2026, 11, 8, 9, 0, tzinfo=UTC)) == "missed"
        session.add(BackupRun(trigger="missed", status="ok", started_at=datetime(2026, 11, 8, 9, 0, tzinfo=UTC), results=[]))
        session.commit()
        assert due_trigger(session, since, datetime(2026, 11, 8, 12, 0, tzinfo=UTC)) is None
        assert due_trigger(session, since, datetime(2026, 11, 9, 3, 0, 10, tzinfo=UTC)) == "schedule"


def test_a_scheduled_backup_is_copied_checked_and_pruned(client, backup_root):
    target = backup_root / "nas"
    target.mkdir()
    (target / "notes.txt").write_text("not a backup")
    (target / "yaap-backup-20200101-000000.zip").write_bytes(b"old")
    assert set_destinations(client, [{"name": "NAS", "kind": "folder", "path": str(target), "keep": 1}]).status_code == 200
    client.app.state.backup_since = datetime(2026, 1, 1, tzinfo=UTC)

    assert backup_tick(client.app, wait=True) == "missed"
    run = client.get("/api/v1/backups/runs").json()[0]
    assert run["status"] == "ok" and run["trigger"] == "missed"
    assert run["results"][0]["ok"] and run["results"][0]["destination"] == "NAS"
    copies = {path.name for path in target.iterdir()}
    assert copies == {run["name"], "notes.txt"}  # the older backup was pruned, the other file left alone
    local = Path(client.app.state.data_dir) / "backups" / run["name"]
    assert sha(local) == sha(target / run["name"])
    assert backup_tick(client.app, wait=True) is None  # nothing is due right after


def test_a_failing_destination_marks_the_run_and_the_header(client, backup_root):
    set_destinations(client, [{"name": "Gone", "kind": "folder", "path": str(backup_root / "missing")}])
    client.app.state.backup_since = datetime(2026, 1, 1, tzinfo=UTC)
    backup_tick(client.app, wait=True)
    status = client.get("/api/v1/backups/status").json()
    assert status["failed"]["status"] == "partial"
    assert "does not exist" in status["failed"]["results"][0]["message"]
    # The backup itself is there; only the copy is missing.
    assert client.get("/api/v1/backups").json()[0]["name"] == status["failed"]["name"]


def test_a_backup_by_hand_is_recorded_and_waits_for_a_pending_restore(client):
    assert client.post("/api/v1/backups").status_code == 201
    assert client.get("/api/v1/backups/runs").json()[0]["trigger"] == "manual"
    (Path(client.app.state.data_dir) / PENDING).write_text("{}")
    assert client.post("/api/v1/backups").status_code == 409
    client.app.state.backup_since = datetime(2026, 1, 1, tzinfo=UTC)
    assert backup_tick(client.app, wait=True) is None


def test_folders_must_be_under_the_backup_root_and_outside_the_data(client, backup_root, tmp_path):
    assert set_destinations(client, [{"name": "Data", "kind": "folder", "path": str(tmp_path / "backups")}]).status_code == 422
    assert set_destinations(client, [{"name": "Rel", "kind": "folder", "path": "backup"}]).status_code == 422
    assert set_destinations(
        client,
        [{"name": "A", "kind": "folder", "path": str(backup_root)}, {"name": "a", "kind": "folder", "path": str(backup_root)}],
    ).status_code == 422


def test_a_backup_comes_back_from_a_destination(client, backup_root):
    set_destinations(client, [{"name": "NAS", "kind": "folder", "path": str(backup_root)}])
    made = client.post("/api/v1/backups").json()
    (Path(client.app.state.data_dir) / "backups" / made["name"]).unlink()
    listed = client.get("/api/v1/backups/destinations").json()[0]
    assert listed["ok"] and [item["name"] for item in listed["backups"]] == [made["name"]]
    fetched = client.post(f"/api/v1/backups/destinations/NAS/fetch/{made['name']}")
    assert fetched.status_code == 200
    assert client.get(f"/api/v1/backups/{made['name']}/restore-check").json()["problems"] == []
    assert client.post(f"/api/v1/backups/destinations/NAS/fetch/{made['name']}").status_code == 409


class FakeShare:
    """smbclient on a local folder, recording how YAAP signed in."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.sessions: list[dict] = []

    def local(self, path: str) -> Path:
        parts = path.lstrip("\\").split("\\")
        assert parts[:2] == ["nas", "backups"], path
        return self.root.joinpath(*parts[2:])

    def install(self, monkeypatch) -> None:
        client = smb.smbclient
        monkeypatch.setattr(client, "register_session", lambda server, **options: self.sessions.append({"server": server, **options}))
        monkeypatch.setattr(client, "makedirs", lambda path, exist_ok=False: self.local(path).mkdir(parents=True, exist_ok=exist_ok))
        monkeypatch.setattr(client, "scandir", lambda path: os.scandir(self.local(path)))
        monkeypatch.setattr(client, "open_file", lambda path, mode="rb": self.local(path).open(mode))
        monkeypatch.setattr(client, "replace", lambda source, target: self.local(source).replace(self.local(target)))
        monkeypatch.setattr(client, "remove", lambda path: self.local(path).unlink())
        monkeypatch.setattr(client, "stat", lambda path: self.local(path).stat())
        monkeypatch.setattr(
            client, "stat_volume", lambda path: type("Volume", (), {"caller_available_size": 123456})()
        )


def test_a_network_share_gets_the_copy_with_the_password_from_the_vault(client, monkeypatch, tmp_path):
    share = FakeShare(tmp_path.parent / f"{tmp_path.name}-share")
    share.root.mkdir()
    share.install(monkeypatch)
    password = "share-password-1234"
    created = client.post("/api/v1/vault/entries", json={"name": "nas", "kind": "smb_password", "value": password})
    assert created.status_code == 201
    destination = {
        "name": "Office NAS", "kind": "smb", "server": "nas", "share": "backups", "folder": "yaap/home",
        "user": "alex", "domain": "OFFICE", "password_entry": "nas",
    }
    assert set_destinations(client, [destination]).status_code == 200

    tested = client.post("/api/v1/backups/destinations/Office NAS/test").json()
    assert tested["ok"] and tested["free_space"] == 123456 and tested["where"] == "\\\\nas\\backups\\yaap\\home"
    made = client.post("/api/v1/backups").json()
    assert (share.root / "yaap" / "home" / made["name"]).is_file()
    assert share.sessions[0]["username"] == "OFFICE\\alex" and share.sessions[0]["password"] == password
    assert share.sessions[0]["encrypt"] is True
    detail = client.get(f"/api/v1/vault/entries/{created.json()['id']}").json()
    assert detail["used_in"] == ["Backup destination Office NAS"]
    assert detail["uses"][0]["used_by"] == "Backup destination Office NAS"
    assert password not in str(client.get("/api/v1/backups/runs").json())


def test_a_share_without_its_password_entry_says_so(client):
    set_destinations(client, [{"name": "NAS", "kind": "smb", "server": "nas", "share": "b", "password_entry": "missing"}])
    state = client.get("/api/v1/backups/destinations").json()[0]
    assert not state["ok"] and "no network share password named missing" in state["message"]
