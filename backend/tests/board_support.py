"""Shared helpers for the board's database tests."""

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.board.models  # noqa: F401  # registers the board tables on the shared base
from app.storage.db import create_session_factory
from app.storage.models import Base


def sessions_in(folder: Path) -> sessionmaker[Session]:
    """A session factory on a new SQLite database with every table, in folder."""
    engine = create_engine(f"sqlite:///{(folder / 'board.db').as_posix()}")
    Base.metadata.create_all(engine)
    return create_session_factory(engine)
