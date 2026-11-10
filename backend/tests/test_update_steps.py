"""The Python steps of the update scripts: when the data must go back, what is busy, the changelog and the version."""

import io
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.backup import BackupError
from app.credentials.crypto import CredentialCipher
from app.scripts import update
from app.storage.db import run_migrations
from app.storage.models import AgentRunRecord, Base, EvalRun, Goal, Replay, ScheduledTask
from app.updates.busy import find_busy
from app.updates.rollback import database_revision, needs_data_rollback, roll_back_data
from app.version import VERSION

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)

CHANGELOG = """# Changelog

## Unreleased

- Main only.

### Action needed

None.

## 0.6.0 (2026-12-01)

- Six.

### Action needed

Run the migration by hand.

## 0.5.8 (2026-11-20)

- Five eight.

### Action needed

None.

## 0.5.7 (2026-11-09)

- Five seven.

### Action needed

Set the thing.
Then restart.
"""


@pytest.mark.parametrize(
    ("revision", "known", "expected"),
    [
        ("b", {"a", "b"}, False),
        ("a", {"a", "b"}, False),
        ("c", {"a", "b"}, True),
        (None, {"a", "b"}, False),
        ("a", set(), True),
        (None, set(), False),
    ],
)
def test_data_goes_back_only_when_the_database_is_ahead_of_the_old_version(
    revision: str | None, known: set[str], expected: bool
) -> None:
    assert needs_data_rollback(revision, known) is expected


def test_a_migrated_database_is_left_alone_by_its_own_version(tmp_path: Path) -> None:
    run_migrations(f"sqlite:///{(tmp_path / 'yaap.db').as_posix()}")
    assert database_revision(tmp_path) is not None
    cipher = CredentialCipher(Fernet.generate_key(), None)
    result = roll_back_data(tmp_path, "yaap-backup-20260101-000000.zip", cipher)
    assert not result.restored and result.safety_backup is None


def test_a_database_ahead_of_the_version_needs_the_named_backup(tmp_path: Path) -> None:
    connection = sqlite3.connect(tmp_path / "yaap.db")
    connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32))")
    connection.execute("INSERT INTO alembic_version VALUES ('from_the_future')")
    connection.commit()
    connection.close()
    cipher = CredentialCipher(Fernet.generate_key(), None)
    with pytest.raises(BackupError):
        roll_back_data(tmp_path, "yaap-backup-20260101-000000.zip", cipher)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as opened:
        yield opened
    engine.dispose()


def run_record(status: str) -> AgentRunRecord:
    return AgentRunRecord(environment_id=1, conversation_id=1, model="m", status=status)


def goal(status: str) -> Goal:
    return Goal(conversation_id=1, objective="o", status=status, max_turns=3, max_cost=0, turns=0)


def eval_run(status: str) -> EvalRun:
    return EvalRun(project_id=1, environment_id=1, status=status, trigger="panel")


def replay(status: str) -> Replay:
    return Replay(
        project_id=1, environment_id=1, source_conversation_id=1, source_step_id=1, step_position=1, snapshot="s",
        status=status,
    )


def task(**changes: object) -> ScheduledTask:
    values: dict[str, object] = {
        "project_id": 1, "name": "nightly", "prompt": "p", "model": "m", "effort": "low", "schedule_kind": "cron",
        "max_turns": 3, "max_cost": 0, "conversation_mode": "new", "enabled": True, "running": False,
        "next_run_at": None,
    }
    return ScheduledTask(**{**values, **changes})


@pytest.mark.parametrize(
    ("busy_row", "idle_row", "kind"),
    [
        (run_record("running"), run_record("complete"), "reply"),
        (goal("active"), goal("done"), "goal"),
        (eval_run("running"), eval_run("done"), "eval_run"),
        (eval_run("queued"), eval_run("stopped"), "eval_run"),
        (replay("running"), replay("error"), "replay"),
        (replay("queued"), replay("done"), "replay"),
        (task(running=True), task(), "scheduled_task"),
        (task(next_run_at=NOW + timedelta(minutes=9)), task(next_run_at=NOW + timedelta(minutes=11)), "scheduled_task"),
        (task(next_run_at=NOW - timedelta(minutes=1)), task(enabled=False, next_run_at=NOW), "scheduled_task"),
    ],
)
def test_only_work_in_progress_or_due_soon_is_busy(
    session: Session, busy_row: object, idle_row: object, kind: str
) -> None:
    session.add(idle_row)
    session.commit()
    assert find_busy(session, NOW) == []
    session.add(busy_row)
    session.commit()
    assert [item.kind for item in find_busy(session, NOW)] == [kind]


def test_the_busy_step_prints_one_line_per_thing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setenv("YAAP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("YAAP_MASTER_KEY", Fernet.generate_key().decode())
    run_migrations(f"sqlite:///{(tmp_path / 'yaap.db').as_posix()}")
    assert update.main(["busy"]) == 0
    assert capsys.readouterr().out == ""


def test_the_changelog_step_lists_action_needed_first_for_the_versions_in_between(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(CHANGELOG))
    assert update.main(["changelog", "0.5.7", "0.6.0"]) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0] == "Action needed:"
    assert lines[1] == "  0.6.0 (2026-12-01): Run the migration by hand."
    assert out.index("Action needed:") < out.index("What changed:")
    assert "0.6.0 (2026-12-01)" in out and "0.5.8 (2026-11-20)" in out
    assert "Five seven" not in out and "Main only" not in out


@pytest.mark.parametrize(
    ("old", "new", "present", "absent"),
    [
        ("0.5.8", "0.6.0", ["Six."], ["Five eight", "Main only"]),
        ("0.5.7", "0.5.8", ["Five eight"], ["Six.", "Five seven"]),
        ("0.5.8", "main", ["Six.", "Main only"], ["Five eight"]),
        ("0.5.7", "0.5.8", ["Action needed: none."], ["Run the migration"]),
    ],
)
def test_the_changelog_step_takes_the_sections_after_the_old_version_up_to_the_new_one(
    tmp_path: Path, capsys, old: str, new: str, present: list[str], absent: list[str]
) -> None:
    path = tmp_path / "CHANGELOG.md"
    path.write_text(CHANGELOG, encoding="utf-8")
    assert update.main(["changelog", old, new, str(path)]) == 0
    out = capsys.readouterr().out
    assert all(text in out for text in present)
    assert not any(text in out for text in absent)


def test_the_changelog_step_fails_with_a_message_when_the_file_is_missing(tmp_path: Path, capsys) -> None:
    assert update.main(["changelog", "0.5.7", "0.6.0", str(tmp_path / "missing.md")]) == 1
    assert "changelog failed" in capsys.readouterr().err


def test_the_version_step_prints_the_running_version(capsys) -> None:
    assert update.main(["version"]) == 0
    assert capsys.readouterr().out.strip() == VERSION
