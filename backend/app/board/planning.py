"""What a board write is made of: the change planned at a head, the opened board it is written to and the clock."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Generic, TypeVar

from app.board.repository import BoardRepository, Change
from app.board.squash import Tally
from app.storage.models import Project

Result = TypeVar("Result")


@dataclass(frozen=True)
class Planned(Generic[Result]):
    """A change built from what was read at a head: the files to write, what it counts as, and what to return."""

    changes: tuple[Change, ...]
    tally: Tally
    result: Result


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


@dataclass(frozen=True)
class Opened:
    project: Project
    repository: BoardRepository
