"""Discarding uncommitted changes in a repository: all of them, or those of one changed file.

Tracked files get their work tree and index back from HEAD; untracked files are removed. Ignored files are never
touched. Only a path that the current status lists as changed can be discarded, so nothing outside the repository or
outside what the user saw in the Changes panel is reached.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.git.service import FileChange, GitError, Repository, repository_status, run_git

# Paths are taken as written, never as pathspec magic or globs (a file may be named `*` or `:(top)`).
LITERAL_PATHSPECS = {"GIT_LITERAL_PATHSPECS": "1"}


@dataclass(frozen=True)
class DiscardPlan:
    """What a discard does: restore these tracked paths and remove these untracked ones; `everything` for all."""

    tracked: tuple[str, ...]
    untracked: tuple[str, ...]
    everything: bool


def plan_discard(changes: Sequence[FileChange], path: str | None) -> DiscardPlan:
    """The discard of every change (path None) or of one changed path. A path that is not a changed file of the
    current status is refused: that also covers paths outside the repository."""
    if path is None:
        selected = list(changes)
        if not selected:
            raise GitError("There are no changes to discard.")
    else:
        selected = [change for change in changes if change.path == path]
        if not selected:
            raise GitError(f"{path} is not one of the changed files.")
    tracked: list[str] = []
    untracked: list[str] = []
    for change in selected:
        if change.state == "untracked":
            untracked.append(change.path)
        else:
            tracked.append(change.path)
            if change.original_path:
                tracked.append(change.original_path)
    return DiscardPlan(tuple(dict.fromkeys(tracked)), tuple(dict.fromkeys(untracked)), path is None)


def apply_discard(folder: Repository, plan: DiscardPlan, has_commits: bool) -> None:
    """Carry out a plan. Without commits there is no HEAD to restore from, so staged files leave the index and are
    removed like untracked ones."""
    tracked = ["."] if plan.everything else list(plan.tracked)
    untracked = list(plan.untracked)
    if plan.tracked:
        if has_commits:
            # Paths that HEAD does not have (a staged new file, a rename's new name) leave index and work tree.
            run_git(["restore", "--source=HEAD", "--staged", "--worktree", "--", *tracked], folder, LITERAL_PATHSPECS)
        else:
            run_git(["rm", "-r", "-f", "-q", "--cached", "--ignore-unmatch", "--", *tracked], folder, LITERAL_PATHSPECS)
            untracked += list(plan.tracked)
    if plan.everything:
        if plan.untracked or (plan.tracked and not has_commits):
            # Without -x ignored files stay; -d also removes folders left empty.
            run_git(["clean", "-f", "-d", "-q"], folder, LITERAL_PATHSPECS)
    elif untracked:
        run_git(["clean", "-f", "-q", "--", *untracked], folder, LITERAL_PATHSPECS)


def discard_summary(path: str | None) -> str:
    return "Before discarding all uncommitted changes" if path is None else f"Before discarding the changes to {path}"


def discard_changes(
    folder: Repository, path: str | None, checkpoint: Callable[[str], int | None] | None = None
) -> int | None:
    """Discard every uncommitted change (path None) or one changed file's. With `checkpoint`, the folder is
    snapshotted first and nothing is discarded when that fails; returns the checkpoint's id."""
    status = repository_status(folder)
    plan = plan_discard(status.changes, path)
    checkpoint_id = None
    if checkpoint is not None:
        checkpoint_id = checkpoint(discard_summary(path))
        if checkpoint_id is None:
            raise GitError("No checkpoint could be taken, so nothing was discarded.")
    apply_discard(folder, plan, status.has_commits)
    return checkpoint_id
