"""After a restart, and periodically: builds cut off by the restart, and copies no longer needed.

A build runs in YAAP's own process, so a restart cuts it off. At start every `building` plan becomes `interrupted`:
the step that was working is open again and the copy is kept, so *Continue* goes on from the last finished step. Its
run is closed as incomplete with every other run cut off by the restart (agents/records.close_stale_runs).

Copies are pruned by `should_prune` (plans/workspace.py): failed and cancelled after a week, done once the branch is
merged or after two weeks, a copy whose plan is gone at once (its conversation was deleted), never one that is or
will be worked on.
"""

import asyncio
import logging
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import anyio
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.git.common import GitError
from app.git.place import git_place
from app.git.service import run_git
from app.plans.model import plan_from_json
from app.plans.models import PlanRecord
from app.plans.progress import build_info_from_json
from app.plans.runner import RESTARTED
from app.plans.store import PlanStore
from app.plans.workspace import BuildCopyError, build_folders, remove_build_copy, should_prune
from app.projects.service import ProjectError, project_path
from app.storage.models import Project

logger = logging.getLogger(__name__)

PRUNE_SECONDS = 60 * 60


def recover_builds(session: Session) -> int:
    """Every building plan becomes interrupted, its working step open again. Returns how many."""
    store = PlanStore(session)
    count = 0
    for record in store.with_status(["building"]):
        if not store.set_status(record.id, "building", "interrupted"):
            continue
        plan = plan_from_json(record.body)
        steps = tuple(replace(step, status="open") if step.status == "working" else step for step in plan.steps)
        store.save_progress(record, replace(plan, steps=steps), build_info_from_json(record.body))
        record.failure = RESTARTED
        count += 1
    session.commit()
    return count


def merged(environment_id: int, folder: Path, record: PlanRecord) -> bool:
    """Whether the build branch is merged into the branch the build started from. Blocking."""
    if not record.build_branch or not record.base_branch:
        return False
    try:
        place = git_place(environment_id, folder)
        found = run_git(
            ["merge-base", "--is-ancestor", f"refs/heads/{record.build_branch}", f"refs/heads/{record.base_branch}"],
            place,
            accept_exit_codes=(0, 1, 128),
        )
    except GitError:
        return False
    return found.exit_code == 0


def prune_project(session: Session, project: Project, folder: Path, now: datetime) -> int:
    """Remove the project's copies that are no longer needed. Blocking."""
    store = PlanStore(session)
    removed = 0
    for plan_id in build_folders(folder):
        record = store.get(plan_id)
        if record is not None and record.project_id != project.id:
            record = None  # a folder named after another project's plan is not this project's copy
        status = record.status if record is not None else None
        done_and_merged = record is not None and status == "done" and merged(project.environment_id, folder, record)
        if not should_prune(status, record.finished_at if record else None, done_and_merged, now):
            continue
        try:
            remove_build_copy(folder, plan_id)
        except BuildCopyError as error:
            logger.warning("A build copy could not be pruned: %s", error)
            continue
        if record is not None:
            record.build_folder = None
        removed += 1
    session.commit()
    return removed


def prune_builds(session: Session, workspace_dir: Path, now: datetime | None = None) -> int:
    """Prune the copies of every project in the workspace. Blocking: call it from a worker thread."""
    now = now or datetime.now(UTC)
    removed = 0
    for project in session.scalars(select(Project)):
        try:
            folder = project_path(workspace_dir, project)
        except ProjectError:
            continue
        if folder.is_dir():
            removed += prune_project(session, project, folder, now)
    return removed


async def prune_builds_periodically(session_factory: sessionmaker[Session], workspace_dir: Path) -> None:
    """Prune copies once an hour, for as long as YAAP runs."""

    def prune() -> None:
        with session_factory() as session:
            prune_builds(session, workspace_dir)

    while True:
        try:
            await anyio.to_thread.run_sync(prune)
        except Exception:  # noqa: BLE001 - a background loop: one failed round is logged, the next one tries again
            logger.warning("Pruning build copies failed.", exc_info=True)
        await asyncio.sleep(PRUNE_SECONDS)
