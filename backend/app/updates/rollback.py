"""Going back to the previous version: whether the database must be restored too, and staging that restore."""

import sqlite3
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.core.backup import BackupInfo, backup_path
from app.core.restore import stage_restore
from app.credentials.crypto import CredentialCipher
from app.storage.db import MIGRATIONS_DIR


@dataclass(frozen=True)
class RollbackResult:
    restored: bool
    safety_backup: BackupInfo | None


def needs_data_rollback(database_revision: str | None, known_revisions: Collection[str]) -> bool:
    """Whether the database was migrated past what this (old) version knows. A database without a revision has
    nothing to undo, and a known revision means the migrations did not get further than this version."""
    return database_revision is not None and database_revision not in known_revisions


def known_revisions() -> frozenset[str]:
    """Every migration revision this version has."""
    config = Config()
    config.set_main_option("script_location", MIGRATIONS_DIR.as_posix())
    return frozenset(script.revision for script in ScriptDirectory.from_config(config).walk_revisions())


def database_revision(data_dir: Path) -> str | None:
    """The migration revision the database file is at; None when there is no database or no revision yet."""
    database = data_dir / "yaap.db"
    if not database.is_file():
        return None
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        connection.close()
    return str(row[0]) if row else None


def roll_back_data(data_dir: Path, backup_name: str, cipher: CredentialCipher) -> RollbackResult:
    """Stage the backup for the next start when the database is ahead of this version; otherwise leave the data."""
    known = known_revisions()
    if not needs_data_rollback(database_revision(data_dir), known):
        return RollbackResult(restored=False, safety_backup=None)
    safety = stage_restore(data_dir, backup_path(data_dir, backup_name), cipher, known.__contains__)
    return RollbackResult(restored=True, safety_backup=safety)
