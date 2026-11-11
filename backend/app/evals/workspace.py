"""The throwaway workspace of one play of an eval case.

A play works in `/data/workspace/<environment>/.evals/<run>/<case>` (later repeats `<case>.<n>`): inside the
environment's folder, so the environment's runner, which mounts only that folder, runs the play's commands there.

- A case that starts at a commit gets a clone of the project at that commit, detached and without a remote. The plan
  named a `git worktree`; a linked worktree's `.git` points into the project's repository, which YAAP's git safety
  refuses (`locate_git_dirs`), which the runner cannot resolve (another absolute path there), and which would let
  the agent move the project's own branches. A clone without hard links shares nothing with the project.
- A case that starts at a checkpoint (one made by replay, or a project that is not a git repository) gets that
  checkpoint's files, read from the project's shadow repository.

A passed play's workspace is removed at once; a failed one is kept for KEEP_FAILED and then pruned by the periodic
loop (`prune_workspaces`).
"""

import shutil
import stat
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

from app.checkpoints.service import checkpoints_dir
from app.checkpoints.shadow import CheckpointError, ShadowRepository
from app.evals.models import StartState
from app.git.service import GitError, extract_archive, run_git

EVALS_FOLDER = ".evals"
KEEP_FAILED = timedelta(hours=24)


class WorkspaceError(Exception):
    """The workspace cannot be made; the message is safe to show."""


def runs_folder(workspace_dir: Path, environment_slug: str) -> Path:
    return workspace_dir / environment_slug / EVALS_FOLDER


def run_folder(workspace_dir: Path, environment_slug: str, run_id: int) -> Path:
    return runs_folder(workspace_dir, environment_slug) / str(run_id)


def play_name(case_name: str, repeat: int) -> str:
    """Case names have no dots, so `<case>.<n>` cannot clash with another case."""
    return case_name if repeat <= 1 else f"{case_name}.{repeat}"


def make_workspace(
    project_folder: Path,
    folder: Path,
    start: StartState,
    data_dir: Path,
    project_id: int,
    tree_of: Callable[[int], str],
) -> None:
    """The workspace at the case's start state. tree_of gives a checkpoint's tree (the caller reads the database)."""
    if folder.exists():
        raise WorkspaceError(f"The workspace {folder.name} already exists.")
    folder.parent.mkdir(parents=True, exist_ok=True)
    if start.commit is not None:
        clone_at(project_folder, folder, start.commit)
        return
    if start.checkpoint is None:
        raise WorkspaceError("The case has no start state.")
    shadow = ShadowRepository(checkpoints_dir(data_dir) / f"{project_id}.git", project_folder)
    try:
        extract_archive(shadow.archive(tree_of(start.checkpoint)), folder)
    except CheckpointError as error:
        remove_folder(folder)
        raise WorkspaceError(f"Checkpoint {start.checkpoint} cannot be read: {error}") from error


def clone_at(project_folder: Path, folder: Path, commit: str) -> None:
    if not (project_folder / ".git").exists():
        raise WorkspaceError(
            "The project is not a git repository, so a case cannot start at a commit; start it at a checkpoint."
        )
    try:
        clone = ["clone", "--quiet", "--no-checkout", "--no-hardlinks", str(project_folder), str(folder)]
        run_git(clone, project_folder, allow_local_transport=True)
        found = run_git(["cat-file", "-t", f"{commit}^{{commit}}"], folder, accept_exit_codes=(0, 1, 128))
        if found.stdout.strip() != "commit":
            raise WorkspaceError(f"The start commit {commit} is not on any branch or tag of the project.")
        run_git(["checkout", "--quiet", "--detach", commit], folder)
        run_git(["remote", "remove", "origin"], folder)
    except GitError as error:
        remove_folder(folder)
        raise WorkspaceError(f"The project cannot be copied at {commit}: {error}") from error
    except WorkspaceError:
        remove_folder(folder)
        raise


def make_writable(function: Callable[[str], object], path: str, _error: BaseException) -> None:
    """git keeps its objects read-only; on Windows they must be made writable to be removed."""
    Path(path).chmod(stat.S_IWRITE)
    function(path)


def remove_folder(folder: Path) -> None:
    if folder.exists():
        shutil.rmtree(folder, onexc=make_writable)


def remove_workspace(workspace_dir: Path, relative: str) -> None:
    """Remove a play's workspace, and its run's folder once that is empty. Only folders under `.evals` go."""
    folder = (workspace_dir / relative).resolve()
    if EVALS_FOLDER not in folder.parts or not folder.is_relative_to(workspace_dir.resolve()):
        raise WorkspaceError("Only an eval workspace can be removed.")
    remove_folder(folder)
    parent = folder.parent
    if parent.name != EVALS_FOLDER and parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()
