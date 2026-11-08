"""Pinned checkpoints: a checkpoint something still starts from is never pruned.

An eval case made by replay starts at a checkpoint; a replay works from one while it runs. Each such owner pins the
checkpoint under its own name ("eval-case:<project>:<name>", "replay:<id>"), so releasing one pin leaves the others.
The pruning in `CheckpointStore.prune` skips every pinned checkpoint, both the 30-day limit and the per-project count.
"""

from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.storage.models import Checkpoint, CheckpointPin


def case_owner(project_id: int, case_name: str) -> str:
    return f"eval-case:{project_id}:{case_name}"


def replay_owner(replay_id: int) -> str:
    return f"replay:{replay_id}"


def pin_checkpoint(session: Session, checkpoint_id: int, owner: str) -> None:
    """Keep the checkpoint for owner; pinning it twice for the same owner is one pin. The caller commits."""
    found = session.scalar(
        select(CheckpointPin.id).where(CheckpointPin.checkpoint_id == checkpoint_id, CheckpointPin.owner == owner)
    )
    if found is None:
        session.add(CheckpointPin(checkpoint_id=checkpoint_id, owner=owner, created_at=datetime.now(UTC)))


def unpin_checkpoint(session: Session, checkpoint_id: int, owner: str) -> None:
    """Release owner's pin; the checkpoint is pruned again like any other once no pin is left. The caller commits."""
    session.execute(
        delete(CheckpointPin).where(CheckpointPin.checkpoint_id == checkpoint_id, CheckpointPin.owner == owner)
    )


def pinned_checkpoints(session: Session, project_id: int) -> set[int]:
    """The project's checkpoints with at least one pin."""
    query = (
        select(CheckpointPin.checkpoint_id)
        .join(Checkpoint, Checkpoint.id == CheckpointPin.checkpoint_id)
        .where(Checkpoint.project_id == project_id)
    )
    return set(session.scalars(query))


def without_pinned(ids: Iterable[int], pinned: set[int]) -> list[int]:
    return [checkpoint_id for checkpoint_id in ids if checkpoint_id not in pinned]
