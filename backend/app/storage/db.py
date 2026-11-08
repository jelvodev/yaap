"""Database engine, sessions and migrations."""

import logging
from collections.abc import Iterator
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
logger = logging.getLogger(__name__)


def load_vector_functions(dbapi_connection) -> bool:  # type: ignore[no-untyped-def]
    """sqlite-vec's functions (vec_distance_cosine) for semantic search. Without them search is full-text only."""
    try:
        import sqlite_vec

        dbapi_connection.enable_load_extension(True)
        try:
            sqlite_vec.load(dbapi_connection)
        finally:
            dbapi_connection.enable_load_extension(False)
        return True
    except Exception:  # noqa: BLE001 - a Python without extension loading still runs YAAP, without vectors
        logger.warning("sqlite-vec could not be loaded; search is full-text only.", exc_info=True)
        return False


def create_database_engine(database_url: str) -> Engine:
    # A writer waits up to 30 seconds for another to finish, instead of failing with "database is locked".
    engine = create_engine(database_url, connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(engine, "connect")
    def configure_sqlite(dbapi_connection, _record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
        load_vector_functions(dbapi_connection)

    return engine


def known_revision(revision: str) -> bool:
    """Whether this YAAP has the migration revision, so a database at it can be brought up to date (a restore)."""
    from alembic.script import ScriptDirectory

    config = Config()
    config.set_main_option("script_location", MIGRATIONS_DIR.as_posix())
    try:
        return ScriptDirectory.from_config(config).get_revision(revision) is not None
    except Exception:  # noqa: BLE001 - alembic raises several kinds for an unknown revision
        return False


def run_migrations(database_url: str) -> None:
    config = Config()
    config.set_main_option("script_location", MIGRATIONS_DIR.as_posix())
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
    finally:
        session.close()
