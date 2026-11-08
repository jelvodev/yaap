"""The files a replayed model call saw: the checkpoint that holds them.

A checkpoint is the project folder just before an action that can change files. The folder as a model call saw it is
therefore the first checkpoint taken at or after the call's start: it is the state just before the next action, which
holds the changes of every action before the call. A checkpoint taken before the call would miss the changes of the
actions between it and the call. When no checkpoint was taken since the call, no action has changed the folder since,
so it is the folder as it is now, and a checkpoint of kind `replay` is taken of it (the project folder itself is only
read).
"""

from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.checkpoints.service import CheckpointStore
from app.storage.models import Checkpoint


class FilesError(Exception):
    """The files of the call cannot be found or saved; the message is safe to show."""


def checkpoint_since(session: Session, project_id: int, started_at: datetime) -> int | None:
    """The project's first checkpoint taken at or after started_at."""
    return session.scalar(
        select(Checkpoint.id)
        .where(Checkpoint.project_id == project_id, Checkpoint.created_at >= started_at)
        .order_by(Checkpoint.id)
        .limit(1)
    )


def files_checkpoint(
    session: Session, store: CheckpointStore, project_id: int, folder: Path, started_at: datetime, position: int
) -> int:
    """The checkpoint holding the files as the model call that started at started_at saw them."""
    found = checkpoint_since(session, project_id, started_at)
    if found is not None:
        return found
    # The store writes through its own session; this one's read transaction ends first, so SQLite has no reader in
    # the way of that write.
    session.commit()
    taken = store.take(project_id, folder, "replay", f"The files for a replay of step {position}", kind="replay")
    if taken is None:
        raise FilesError("The project's files could not be saved for the replay; see the log.")
    return taken
