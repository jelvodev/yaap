"""Restoring a backup.

A restore happens in two halves, because the database is open while YAAP runs:

1. While running: check the backup (a YAAP backup, not newer than this YAAP, made with this master key), unpack it into
   `data/.restore-staging`, make a fresh backup of the current state, and write `data/.restore-pending.json`. Then
   YAAP stops; Docker starts it again (`restart: unless-stopped`).
2. On start, before the database is opened: the current database and folders move to `data/.restore-previous` (the
   one of an earlier restore is removed), the staged ones take their place, and the migrations bring an older backup
   up to date.

Not part of a backup, so untouched: the master key, the Claude Code and Codex sign-ins, the checkpoints.
"""

import json
import logging
import shutil
import sqlite3
import tempfile
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from app.core.backup import INCLUDED_FOLDERS, BackupError, BackupInfo, create_backup
from app.credentials.crypto import CredentialCipher, CredentialDecryptionError

logger = logging.getLogger(__name__)

STAGING = ".restore-staging"
PENDING = ".restore-pending.json"
PREVIOUS = ".restore-previous"
DATABASE = "yaap.db"
DATABASE_FILES = (DATABASE, f"{DATABASE}-wal", f"{DATABASE}-shm")
ENCRYPTED_TABLES = (
    ("vault_entries", "encrypted_value"),
    ("credentials", "encrypted_value"),
    ("secrets", "encrypted_value"),
)


class RestoreError(Exception):
    """The backup cannot be restored; the message says why."""


@dataclass
class RestoreCheck:
    created_at: datetime | None
    database_revision: str | None
    files: dict[str, int] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    @property
    def restorable(self) -> bool:
        return not self.problems


def safe_member(name: str) -> bool:
    """Only the database, the manifest and the backed-up folders, without absolute paths or '..'."""
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        return False
    return name in (DATABASE, "manifest.json") or path.parts[0] in INCLUDED_FOLDERS


def read_manifest(archive: zipfile.ZipFile) -> dict:
    try:
        manifest = json.loads(archive.read("manifest.json"))
    except (KeyError, ValueError) as error:
        raise RestoreError("This zip is not a YAAP backup: it has no readable manifest.json.") from error
    if not isinstance(manifest, dict) or manifest.get("yaap_backup") != 1:
        raise RestoreError("This zip is not a YAAP backup this version can read.")
    if DATABASE not in archive.namelist():
        raise RestoreError("This backup has no database.")
    return manifest


def decrypts(database: Path, cipher: CredentialCipher) -> bool | None:
    """Whether the master key decrypts a stored value of the backup; None when the backup has none."""
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        for table, column in ENCRYPTED_TABLES:
            try:
                row = connection.execute(f"SELECT {column} FROM {table} LIMIT 1").fetchone()  # noqa: S608 - fixed names
            except sqlite3.Error:
                continue
            if row is not None:
                try:
                    cipher.decrypt(row[0])
                    return True
                except CredentialDecryptionError:
                    return False
        return None
    finally:
        connection.close()


def check_backup(path: Path, cipher: CredentialCipher, known_revision: Callable[[str], bool]) -> RestoreCheck:
    """What a restore of this backup would bring back, and every reason it cannot."""
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as error:
        return RestoreCheck(None, None, problems=[f"The file is not a readable zip: {error}"])
    with archive:
        try:
            manifest = read_manifest(archive)
        except RestoreError as error:
            return RestoreCheck(None, None, problems=[str(error)])
        created = manifest.get("created_at")
        check = RestoreCheck(
            datetime.fromisoformat(created) if isinstance(created, str) else None,
            manifest.get("database_revision"),
            {str(key): int(value) for key, value in (manifest.get("files") or {}).items()},
        )
        unsafe = [name for name in archive.namelist() if not safe_member(name) and not name.endswith("/")]
        if unsafe:
            check.problems.append(f"The backup holds files outside YAAP's data: {', '.join(unsafe[:3])}.")
        if check.database_revision is None or not known_revision(check.database_revision):
            check.problems.append(
                "The backup comes from a newer YAAP (or an unknown one). Update YAAP first, then restore it."
            )
        with tempfile.TemporaryDirectory(prefix="yaap-restore-check-") as scratch:
            database = Path(scratch) / DATABASE
            with archive.open(DATABASE) as source, database.open("wb") as target:
                shutil.copyfileobj(source, target)
            try:
                works = decrypts(database, cipher)
            except sqlite3.Error as error:
                check.problems.append(f"The backup's database cannot be read: {error}")
                works = None
            if works is False:
                check.problems.append(
                    "The backup was made with another master key: its keys and secrets cannot be decrypted. Start "
                    "YAAP with the YAAP_MASTER_KEY of that time (or put it in YAAP_MASTER_KEY_PREVIOUS) to restore it."
                )
    return check


def stage_restore(
    data_dir: Path, path: Path, cipher: CredentialCipher, known_revision: Callable[[str], bool]
) -> BackupInfo:
    """Unpack the backup next to the live data, make a fresh backup of the current state, and mark the restore for
    the next start. Returns the fresh backup."""
    check = check_backup(path, cipher, known_revision)
    if not check.restorable:
        raise RestoreError(" ".join(check.problems))
    staging = data_dir / STAGING
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                if member.is_dir() or not safe_member(member.filename):
                    continue
                target = staging / member.filename
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
        # Unpacked first: the fresh backup may prune old backups, the one being restored among them.
        try:
            safety = create_backup(data_dir)
        except BackupError:
            # Backups are named by the second; one was just made.
            time.sleep(1.1)
            safety = create_backup(data_dir)
    except (OSError, zipfile.BadZipFile, BackupError) as error:
        shutil.rmtree(staging, ignore_errors=True)
        raise RestoreError(f"The restore could not be prepared: {error}") from error
    (data_dir / PENDING).write_text(
        json.dumps({"backup": path.name, "safety_backup": safety.name, "staged_at": datetime.now(UTC).isoformat()}),
        encoding="utf-8",
    )
    return safety


def apply_pending_restore(data_dir: Path) -> bool:
    """On start, before the database opens: put a staged restore in place. Returns whether one was applied."""
    marker = data_dir / PENDING
    staging = data_dir / STAGING
    if not marker.is_file():
        return False
    if not (staging / DATABASE).is_file():
        logger.error("A restore was pending, but its staged files are missing; nothing was changed.")
        marker.unlink(missing_ok=True)
        return False
    previous = data_dir / PREVIOUS
    shutil.rmtree(previous, ignore_errors=True)
    previous.mkdir()
    moved: list[tuple[Path, Path]] = []
    try:
        for name in (*DATABASE_FILES, *INCLUDED_FOLDERS):
            current = data_dir / name
            if current.exists():
                shutil.move(current, previous / name)
                moved.append((previous / name, current))
        for name in (DATABASE, *INCLUDED_FOLDERS):
            staged = staging / name
            if staged.exists():
                shutil.move(staged, data_dir / name)
                moved.append((data_dir / name, staged))
    except OSError:
        # Put everything back as it was; the restore stays pending and is tried again on the next start.
        for source, target in reversed(moved):
            shutil.move(source, target)
        logger.exception("Putting the restored backup in place failed; YAAP starts with the data it had.")
        return False
    shutil.rmtree(staging, ignore_errors=True)
    marker.unlink(missing_ok=True)
    logger.warning("Restored a backup; the data from before is in %s until the next restore.", previous)
    return True
