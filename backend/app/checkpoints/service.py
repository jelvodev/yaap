"""Checkpoints of project folders: take, compare, roll back and prune.

Before each tool call that can change files in a project, the run takes a checkpoint: the folder as it is just before
the action. "Changes of an action" is the difference from its checkpoint to the next one (or to the folder as it is
now). A roll back first takes a `restore` checkpoint of the current state, so it can be undone, and then makes the
folder match the chosen checkpoint. Only files are restored: what a command did elsewhere is not undone.
"""

import json
import logging
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.checkpoints.pins import pinned_checkpoints, without_pinned
from app.checkpoints.shadow import CheckpointError, ShadowRepository
from app.storage.models import Checkpoint

logger = logging.getLogger(__name__)

# Tool calls that can change files in the project folder.
CHANGING_TOOLS = frozenset(
    {"write_file", "edit_file", "rename_symbol", "run_command", "start_process", "import_attachment", "install_skill"}
)
KEEP_DAYS = 30
KEEP_PER_PROJECT = 500
FNV_OFFSET = 0xCBF29CE484222325
FNV_PRIME = 0x100000001B3


def call_key(tool: str, arguments: dict[str, Any]) -> str:
    """A short key for a tool call: FNV-1a over the name and the arguments as sorted, compact JSON. The chat computes
    the same key from a tool call block to find its checkpoint."""
    text = tool + "\n" + json.dumps(arguments, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    value = FNV_OFFSET
    for byte in text.encode("utf-8"):
        value = ((value ^ byte) * FNV_PRIME) & 0xFFFFFFFFFFFFFFFF
    return f"{value:016x}"


def checkpoints_dir(data_dir: Path) -> Path:
    return data_dir / "checkpoints"


@dataclass
class CheckpointStore:
    """What a run needs to take checkpoints, and what the API needs to show and restore them."""

    session_factory: sessionmaker[Session]
    data_dir: Path

    def repository(self, project_id: int, folder: Path) -> ShadowRepository:
        return ShadowRepository(checkpoints_dir(self.data_dir) / f"{project_id}.git", folder)

    def take(
        self,
        project_id: int,
        folder: Path,
        tool: str,
        summary: str,
        *,
        conversation_id: int | None = None,
        run_record_id: int | None = None,
        key: str | None = None,
        kind: str = "action",
    ) -> int | None:
        """Take a checkpoint; returns its id, or None when it failed (logged; the action goes ahead)."""
        try:
            repository = self.repository(project_id, folder)
            tree = repository.snapshot_tree()
            with self.session_factory() as session:
                previous = session.scalar(
                    select(Checkpoint).where(Checkpoint.project_id == project_id).order_by(Checkpoint.id.desc())
                )
                # The same state as the last checkpoint: share its snapshot.
                commit = previous.commit if previous is not None and previous.tree == tree else None
                if commit is None:
                    commit = repository.commit(tree, f"{kind}: {summary[:200]}")
                checkpoint = Checkpoint(
                    project_id=project_id,
                    conversation_id=conversation_id,
                    run_record_id=run_record_id,
                    kind=kind,
                    tool=tool[:60],
                    summary=summary[:300],
                    call_key=key,
                    commit=commit,
                    tree=tree,
                    created_at=datetime.now(UTC),
                )
                session.add(checkpoint)
                session.commit()
                repository.keep(checkpoint.id, commit)
                self.prune(session, repository, project_id)
                return checkpoint.id
        except (CheckpointError, OSError) as error:
            logger.warning("No checkpoint for project %s before %s: %s", project_id, tool, error)
            return None

    def prune(self, session: Session, repository: ShadowRepository, project_id: int) -> None:
        """Remove checkpoints older than KEEP_DAYS and beyond the newest KEEP_PER_PROJECT; pinned ones always stay
        and do not count towards the number."""
        cutoff = datetime.now(UTC) - timedelta(days=KEEP_DAYS)
        pinned = pinned_checkpoints(session, project_id)
        ids = without_pinned(
            session.scalars(
                select(Checkpoint.id).where(Checkpoint.project_id == project_id).order_by(Checkpoint.id.desc())
            ),
            pinned,
        )
        old = set(ids[KEEP_PER_PROJECT:])
        old.update(
            without_pinned(
                session.scalars(
                    select(Checkpoint.id).where(Checkpoint.project_id == project_id, Checkpoint.created_at < cutoff)
                ),
                pinned,
            )
        )
        if not old:
            return
        session.execute(delete(Checkpoint).where(Checkpoint.id.in_(old)))
        session.commit()
        repository.drop(sorted(old))

    def next_tree(self, session: Session, checkpoint: Checkpoint, folder: Path) -> str:
        """The state after the checkpoint's action: the next checkpoint, or the folder as it is now."""
        following = session.scalar(
            select(Checkpoint)
            .where(Checkpoint.project_id == checkpoint.project_id, Checkpoint.id > checkpoint.id)
            .order_by(Checkpoint.id)
        )
        if following is not None:
            return following.tree
        return self.repository(checkpoint.project_id, folder).snapshot_tree()

    def action_diff(self, session: Session, checkpoint: Checkpoint, folder: Path) -> str:
        repository = self.repository(checkpoint.project_id, folder)
        return repository.diff(checkpoint.tree, self.next_tree(session, checkpoint, folder))

    def restore_preview(self, checkpoint: Checkpoint, folder: Path) -> tuple[str, list[str]]:
        """What a roll back would change: the diff from now back to the checkpoint, and the paths."""
        repository = self.repository(checkpoint.project_id, folder)
        current = repository.snapshot_tree()
        return repository.diff(current, checkpoint.tree), repository.changed_paths(current, checkpoint.tree)

    def restore(self, checkpoint: Checkpoint, folder: Path, conversation_id: int | None) -> int | None:
        """Roll the folder back to the checkpoint; returns the id of the checkpoint of the state before."""
        before = self.take(
            checkpoint.project_id,
            folder,
            "restore",
            f"Before restoring checkpoint {checkpoint.id}",
            conversation_id=conversation_id,
            kind="restore",
        )
        if before is None:
            raise CheckpointError("The current state could not be saved first, so nothing was restored.")
        repository = self.repository(checkpoint.project_id, folder)
        with self.session_factory() as session:
            current = session.get_one(Checkpoint, before).tree
        repository.restore(checkpoint.tree, current)
        return before


def count_for_project(session: Session, project_id: int) -> int:
    return session.scalar(select(func.count(Checkpoint.id)).where(Checkpoint.project_id == project_id)) or 0


def remove_project_checkpoints(data_dir: Path, project_id: int) -> None:
    shutil.rmtree(checkpoints_dir(data_dir) / f"{project_id}.git", ignore_errors=True)
