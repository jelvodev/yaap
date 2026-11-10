"""The Python half of scripts/update.ps1 and scripts/update.sh: the steps whose decisions are not git or Docker.

Inside the container: `docker compose exec -T yaap python -m app.scripts.update <step>`. Steps:
    busy                        one line per thing that is working now (nothing printed: idle)
    backup                      make a backup and print its name
    rollback <backup>           stage the backup when the database is ahead of this version; prints "restored" or "kept"
    version                     print this version
    changelog <from> <to> [path]  the changelog between two versions (a CHANGELOG.md path, or standard input)
Every step prints plain lines and exits non-zero with a message on standard error when it fails.
"""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from app.config import load_settings
from app.core.backup import BackupError, create_backup
from app.core.restore import RestoreError
from app.credentials.crypto import CredentialCipher
from app.storage.db import create_database_engine, create_session_factory
from app.updates import busy, changelog, rollback
from app.version import VERSION


class StepError(Exception):
    """A step failed; the message says why."""


def step_busy() -> int:
    settings = load_settings()
    engine = create_database_engine(settings.database_url)
    try:
        with create_session_factory(engine)() as session:
            items = busy.find_busy(session, datetime.now(UTC))
    except SQLAlchemyError as error:
        raise StepError(f"The database cannot be read: {error}") from error
    finally:
        engine.dispose()
    for item in items:
        print(f"{item.kind}: {item.label}")
    return 0


def step_backup() -> int:
    print(create_backup(load_settings().data_dir).name)
    return 0


def step_rollback(backup: str) -> int:
    settings = load_settings()
    cipher = CredentialCipher(settings.master_key, settings.previous_master_key)
    result = rollback.roll_back_data(settings.data_dir, backup, cipher)
    print("restored" if result.restored else "kept")
    if result.safety_backup is not None:
        print(f"backup of the state before: {result.safety_backup.name}")
    return 0


def step_changelog(old: str, new: str, path: str | None) -> int:
    try:
        text = Path(path).read_text(encoding="utf-8") if path else sys.stdin.read()
    except OSError as error:
        raise StepError(f"The changelog cannot be read: {error}") from error
    sys.stdout.write(changelog.render_between(text, old, new))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Steps of the update scripts.")
    steps = parser.add_subparsers(dest="step", required=True)
    steps.add_parser("busy")
    steps.add_parser("backup")
    steps.add_parser("version")
    rolled = steps.add_parser("rollback")
    rolled.add_argument("backup")
    between = steps.add_parser("changelog")
    between.add_argument("old")
    between.add_argument("new")
    between.add_argument("path", nargs="?")
    arguments = parser.parse_args(argv)
    try:
        if arguments.step == "busy":
            return step_busy()
        if arguments.step == "backup":
            return step_backup()
        if arguments.step == "rollback":
            return step_rollback(arguments.backup)
        if arguments.step == "changelog":
            return step_changelog(arguments.old, arguments.new, arguments.path)
        print(VERSION)
        return 0
    except (StepError, BackupError, RestoreError) as error:
        print(f"{arguments.step} failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
