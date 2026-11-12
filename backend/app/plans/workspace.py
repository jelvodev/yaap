"""The copy a build works in, inside the project folder, and how its branch comes back.

A build never works in the user's own working tree. It gets a copy at `.yaap/builds/<plan id>`: a repository of its
own (its own `.git` folder, so `check_repository` accepts it as an ordinary repository; a linked worktree would be
refused), holding the history of the project's commit the build starts from. The history travels as a git bundle
made where git runs for the project; the copy takes the objects and gets the build branch at that commit.

Coming back works the same way in reverse, and only through the bundle: YAAP reads the bundle's list of refs itself
and accepts exactly the build branch at the commit the build recorded, which must descend from the base commit. The
copy's configuration, hooks and other refs never reach the project; git refuses to run in the copy at all once its
configuration sets something YAAP does not honour. The branch is created in the project only when it does not exist.

`.yaap/builds/.gitignore` (written by YAAP, "*") keeps the copies out of the project's `git status` without touching
the user's own `.gitignore` or writing into the project's `.git`, which processes in the runner can change.

Call these from a worker thread: git for a project in the workspace runs in the runner.
"""

import logging
import re
import shutil
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from app.evals.workspace import make_writable
from app.git.bundles import BRANCH_PREFIX, bundle_create, unbundle
from app.git.common import GitError
from app.git.service import Repository, folder_of, run_git
from app.plans.model import PlanStatus
from app.targets.safe_root import SafeRoot, SafeRootError

logger = logging.getLogger(__name__)

YAAP_FOLDER = ".yaap"
BUILDS_FOLDER = f"{YAAP_FOLDER}/builds"
BUILDS_IGNORE = f"{BUILDS_FOLDER}/.gitignore"
COPY_TIMEOUT_SECONDS = 600
_COMMIT = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
_BUNDLE_SIGNATURES = (b"# v2 git bundle\n", b"# v3 git bundle\n")

KEEP_FAILED = timedelta(days=7)
KEEP_DONE = timedelta(days=14)
# A copy that is still worked on, or will be, is never pruned.
KEPT_STATUSES: frozenset[PlanStatus] = frozenset({"draft", "approved", "queued", "building", "interrupted"})

# Where git runs for a folder of the project's environment (app.git.place.git_place, bound to the environment).
PlaceOf = Callable[[Path], Repository]


class BuildCopyError(Exception):
    """The copy cannot be made or its branch cannot come back; the message is git's or YAAP's sentence."""


@dataclass(frozen=True)
class BuildBase:
    """Where a build starts: the project's checked-out branch (None on a detached HEAD), its commit, and how many
    uncommitted changes the project has (they are not part of the build)."""

    branch: str | None
    commit: str
    uncommitted: int


def builds_folder(project_folder: Path) -> Path:
    return project_folder / BUILDS_FOLDER


def copy_relative(plan_id: int) -> str:
    """The copy's folder relative to the project folder, as the plan row stores it."""
    return f"{BUILDS_FOLDER}/{plan_id}"


def copy_folder(project_folder: Path, plan_id: int) -> Path:
    return builds_folder(project_folder) / str(plan_id)


def read_base(place: Repository) -> BuildBase:
    """The project's current commit and branch. Raises BuildCopyError when it has no commit to build on."""
    head = run_git(["rev-parse", "--verify", "--quiet", "HEAD^{commit}"], place, accept_exit_codes=(0, 1))
    commit = head.stdout.strip()
    if head.exit_code != 0 or not _COMMIT.fullmatch(commit):
        raise BuildCopyError("The project's repository has no commit yet; commit its files first.")
    branch = run_git(["symbolic-ref", "--quiet", "--short", "HEAD"], place, accept_exit_codes=(0, 1)).stdout.strip()
    status = run_git(["status", "--porcelain", "--untracked-files=all"], place).stdout
    uncommitted = sum(1 for line in status.splitlines() if line.strip())
    return BuildBase(branch or None, commit, uncommitted)


def branch_tip(place: Repository, branch: str) -> str | None:
    """The commit the project's branch points at, or None when it does not exist."""
    found = run_git(["rev-parse", "--verify", "--quiet", f"{BRANCH_PREFIX}{branch}"], place, accept_exit_codes=(0, 1))
    return found.stdout.strip() if found.exit_code == 0 else None


def bundle_header(data: bytes) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    """The prerequisite commits and the (commit, ref) pairs of a bundle, read from its text header by YAAP itself."""
    signature = next((sig for sig in _BUNDLE_SIGNATURES if data.startswith(sig)), None)
    if signature is None:
        raise BuildCopyError("The build's bundle is not a git bundle.")
    end = data.find(b"\n\n", len(signature) - 1)
    if end < 0:
        raise BuildCopyError("The build's bundle has no complete header.")
    prerequisites: list[str] = []
    refs: list[tuple[str, str]] = []
    for raw in data[len(signature) : end].decode("utf-8", errors="replace").split("\n"):
        if not raw or raw.startswith("@"):  # v3 capabilities
            continue
        if raw.startswith("-"):
            prerequisites.append(raw[1:].split(" ", 1)[0])
            continue
        sha, _, ref = raw.partition(" ")
        refs.append((sha, ref))
    return tuple(prerequisites), tuple(refs)


def check_build_bundle(refs: Sequence[tuple[str, str]], branch: str, commit: str) -> None:
    """A build's bundle holds exactly its branch at the commit the build recorded, nothing else."""
    if list(refs) != [(commit, BRANCH_PREFIX + branch)]:
        raise BuildCopyError(
            f"The build's bundle does not hold exactly {branch} at {commit[:12]}; nothing was brought into the project."
        )


def write_builds_ignore(project_folder: Path) -> None:
    """`.yaap/builds/.gitignore` with "*", written link-safely, so the copies stay out of the project's status."""
    try:
        with SafeRoot(project_folder) as root:
            root.ensure_directory(BUILDS_FOLDER)
            if not root.is_file(BUILDS_IGNORE) or root.read_bytes(BUILDS_IGNORE) != b"*\n":
                root.write_bytes(BUILDS_IGNORE, b"*\n")
    except (SafeRootError, OSError) as error:
        raise BuildCopyError(f"The build folder cannot be prepared: {error}") from error


def make_build_copy(place: Repository, place_of: PlaceOf, plan_id: int, base_commit: str, branch: str) -> Path:
    """Make `.yaap/builds/<plan id>` a repository with the project's history up to `base_commit` and the build
    branch checked out there. Nothing is left behind when it fails."""
    project_folder = folder_of(place)
    relative = copy_relative(plan_id)
    write_builds_ignore(project_folder)
    try:
        with SafeRoot(project_folder) as root:
            if root.stat(relative) is not None:
                raise BuildCopyError(f"The build folder {relative} already exists.")
            root.ensure_directory(relative)
    except (SafeRootError, OSError) as error:
        raise BuildCopyError(f"The build folder cannot be made: {error}") from error
    folder = copy_folder(project_folder, plan_id)
    try:
        data = bundle_create(place, ["HEAD"], timeout=COPY_TIMEOUT_SECONDS)
        _, refs = bundle_header(data)
        if (base_commit, "HEAD") not in refs:
            raise BuildCopyError("The project's branch moved while the build was prepared; approve the plan again.")
        copy = place_of(folder)
        run_git(["init", "--quiet", "-b", branch], copy)
        unbundle(copy, data, timeout=COPY_TIMEOUT_SECONDS)
        run_git(["update-ref", f"{BRANCH_PREFIX}{branch}", base_commit, ""], copy)
        run_git(["reset", "--hard", "--quiet", "HEAD"], copy, timeout=COPY_TIMEOUT_SECONDS)
    except (GitError, BuildCopyError) as error:
        remove_build_copy(project_folder, plan_id)
        if isinstance(error, BuildCopyError):
            raise
        raise BuildCopyError(str(error)) from error
    return folder


def copy_tip(copy: Repository, branch: str) -> str:
    """The commit the build branch points at in the copy."""
    return run_git(["rev-parse", "--verify", f"{BRANCH_PREFIX}{branch}^{{commit}}"], copy).stdout.strip()


def deliver_build(
    place: Repository, copy: Repository, branch: str, base_commit: str, commit: str, previous: str | None = None
) -> bool:
    """Bring the build branch at `commit` (the commit the build recorded) into the project. The branch is created,
    or, for a build that goes on after it delivered `previous`, moved on from exactly that commit. False when there is
    nothing new to bring. Raises BuildCopyError when the branch exists otherwise, the bundle holds anything else, or
    the commit does not descend from the base."""
    if commit in (base_commit, previous):
        return False
    try:
        existing = branch_tip(place, branch)
        if existing is not None and existing != previous:
            raise BuildCopyError(f"The branch {branch} already exists in the project; it was left as it is.")
        data = bundle_create(copy, [f"{BRANCH_PREFIX}{branch}", f"^{base_commit}"], timeout=COPY_TIMEOUT_SECONDS)
        check_build_bundle(bundle_header(data)[1], branch, commit)
        check_build_bundle(unbundle(place, data, timeout=COPY_TIMEOUT_SECONDS), branch, commit)
        descends = run_git(["merge-base", "--is-ancestor", base_commit, commit], place, accept_exit_codes=(0, 1))
        if descends.exit_code != 0:
            raise BuildCopyError(f"{commit[:12]} does not descend from the build's base commit; nothing was brought.")
        run_git(["update-ref", f"{BRANCH_PREFIX}{branch}", commit, existing or ""], place)
    except GitError as error:
        raise BuildCopyError(str(error)) from error
    return True


def remove_build_copy(project_folder: Path, plan_id: int) -> None:
    """Remove a build's copy. Only a folder directly below `.yaap/builds` is removed, never through a link."""
    relative = copy_relative(plan_id)
    if sys.platform == "win32":
        # Windows: git keeps objects read-only, which SafeRoot's removal does not handle; YAAP runs on Linux, so
        # this path serves development and tests.
        folder = copy_folder(project_folder, plan_id)
        builds = builds_folder(project_folder)
        if builds.is_symlink() or folder.is_symlink() or not folder.resolve().is_relative_to(builds.resolve()):
            raise BuildCopyError("Only a build folder can be removed.")
        if folder.exists():
            shutil.rmtree(folder, onexc=make_writable)
        return
    try:
        with SafeRoot(project_folder) as root:
            if root.stat(relative) is not None:
                root.remove_tree(relative)
    except (SafeRootError, OSError) as error:
        raise BuildCopyError(f"The build folder {relative} cannot be removed: {error}") from error


def build_folders(project_folder: Path) -> list[int]:
    """The plan ids of the copies in the project's build folder."""
    builds = builds_folder(project_folder)
    if builds.is_symlink() or not builds.is_dir():
        return []
    return sorted(int(entry.name) for entry in builds.iterdir() if entry.name.isdigit() and not entry.is_symlink())


def should_prune(status: PlanStatus | None, finished_at: datetime | None, merged: bool, now: datetime) -> bool:
    """Whether a build's copy goes: failed and cancelled after KEEP_FAILED; done once its branch is merged or after
    KEEP_DONE; a copy whose plan no longer exists at once; never one that is or will be worked on."""
    if status is None:
        return True
    if status in KEPT_STATUSES:
        return False
    age = now - finished_at if finished_at is not None else KEEP_DONE
    if status == "done":
        return merged or age > KEEP_DONE
    return age > KEEP_FAILED
