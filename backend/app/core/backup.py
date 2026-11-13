"""Backups of YAAP's data folder.

A backup is one zip in `data/backups`: a consistent copy of the database (SQLite's online backup API, so it is safe
while YAAP runs), the Blueprint layers with their git history, the attachments, the plugins, the
project folders and the boards kept in separate repositories. Left out:
the Claude Code sign-in (sign in again after a restore), heavy rebuildable folders such as node_modules, and the
master key, which lives outside the data folder and is backed up separately. Restoring: core.restore.
"""

import json
import os
import re
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

BACKUP_FOLDER = "backups"
BACKUP_NAME = re.compile(r"^yaap-backup-\d{8}-\d{6}\.zip$")
DEFAULT_KEEP = 7
# Folders that are rebuilt from the project itself and only make a backup large.
SKIPPED_FOLDERS = frozenset(
    {"node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".next", "dist", "build", "target"}
)
INCLUDED_FOLDERS = ("blueprint", "boards", "files", "knowledge", "plugins", "workspace")


class BackupError(Exception):
    """The backup could not be made or found; the message says why."""


@dataclass
class BackupInfo:
    name: str
    size: int
    created_at: datetime


def backups_dir(data_dir: Path) -> Path:
    return data_dir / BACKUP_FOLDER


def info(path: Path) -> BackupInfo:
    stamp = datetime.strptime(path.name[len("yaap-backup-") : -len(".zip")], "%Y%m%d-%H%M%S").replace(tzinfo=UTC)
    return BackupInfo(name=path.name, size=path.stat().st_size, created_at=stamp)


def list_backups(data_dir: Path) -> list[BackupInfo]:
    folder = backups_dir(data_dir)
    if not folder.is_dir():
        return []
    found = [info(path) for path in folder.iterdir() if path.is_file() and BACKUP_NAME.match(path.name)]
    return sorted(found, key=lambda item: item.name, reverse=True)


def backup_path(data_dir: Path, name: str) -> Path:
    if not BACKUP_NAME.match(name):
        raise BackupError("Unknown backup.")
    path = backups_dir(data_dir) / name
    if not path.is_file():
        raise BackupError("Unknown backup.")
    return path


def copy_database(database_file: Path, target: Path) -> str | None:
    """A consistent copy of the live database; returns its migration revision."""
    source = sqlite3.connect(f"file:{database_file.as_posix()}?mode=ro", uri=True)
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
        row = destination.execute("SELECT version_num FROM alembic_version").fetchone()
        return row[0] if row else None
    finally:
        destination.close()
        source.close()


def add_folder(archive: zipfile.ZipFile, data_dir: Path, folder: Path) -> int:
    count = 0
    for root, folders, files in os.walk(folder, followlinks=False):
        folders[:] = sorted(name for name in folders if name not in SKIPPED_FOLDERS)
        for name in sorted(files):
            path = Path(root) / name
            if path.is_symlink() or not path.is_file():
                continue
            archive.write(path, path.relative_to(data_dir).as_posix())
            count += 1
    return count


def create_backup(data_dir: Path, keep: int = DEFAULT_KEEP, now: datetime | None = None) -> BackupInfo:
    now = now or datetime.now(UTC)
    folder = backups_dir(data_dir)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"yaap-backup-{now:%Y%m%d-%H%M%S}.zip"
    target = folder / name
    if target.exists():
        raise BackupError("A backup was made this second already; try again.")
    partial = folder / f".{name}.partial"
    try:
        with tempfile.TemporaryDirectory(prefix="yaap-backup-") as scratch:
            database_copy = Path(scratch) / "yaap.db"
            revision = copy_database(data_dir / "yaap.db", database_copy)
            with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                archive.write(database_copy, "yaap.db")
                counts = {
                    included: add_folder(archive, data_dir, data_dir / included)
                    for included in INCLUDED_FOLDERS
                    if (data_dir / included).is_dir()
                }
                manifest = {
                    "yaap_backup": 1,
                    "created_at": now.isoformat(),
                    "database_revision": revision,
                    "files": counts,
                    "skipped_folders": sorted(SKIPPED_FOLDERS),
                    "not_included": [
                        "the master key (YAAP_MASTER_KEY)",
                        "the Claude Code sign-in (claude-code/)",
                        "the Codex sign-in (codex/)",
                        "the installed Claude Code and Codex (tools/)",
                    ],
                }
                archive.writestr("manifest.json", json.dumps(manifest, indent=2))
        partial.replace(target)
    except (OSError, sqlite3.Error, zipfile.BadZipFile) as error:
        partial.unlink(missing_ok=True)
        raise BackupError(f"The backup failed: {error}") from error
    prune(data_dir, keep)
    return info(target)


def prune(data_dir: Path, keep: int) -> None:
    """Keep the newest backups; older ones are removed."""
    for old in list_backups(data_dir)[max(keep, 1) :]:
        (backups_dir(data_dir) / old.name).unlink(missing_ok=True)
