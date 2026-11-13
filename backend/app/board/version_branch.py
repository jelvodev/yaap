"""Git for a version's branch: `yaap/v<version>` in the project, and the copy a card's branch is merged in.

A version branch starts at the default branch's head when the first card of the version is merged. Merging works the
way a build does (app.plans.workspace): in a copy at `.yaap/builds/_v<version>`, a repository of its own that holds the
version branch and the card's branch, brought there as a bundle and checked by YAAP. The merged version branch comes
back the same way, as a bundle with exactly that one ref, and moves in the project with a compare-and-swap, so a
version branch that moved meanwhile is never overwritten. The default branch, the checked-out branch and every other
ref are never written, and nothing is fetched or pushed.

Call these from a worker thread: git for a project in the workspace runs in the runner.
"""

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from app.board.paths import VERSION_NAME
from app.git.bundles import BRANCH_PREFIX, bundle_create, safe_branch_name, unbundle
from app.git.common import GitError
from app.git.service import Repository, folder_of, run_git
from app.plans.workspace import (
    COPY_TIMEOUT_SECONDS,
    BuildCopyError,
    PlaceOf,
    branch_tip,
    bundle_header,
    check_build_bundle,
    copy_folder,
    copy_relative,
    remove_build_copy,
    write_builds_ignore,
)
from app.targets.safe_root import SafeRoot, SafeRootError

logger = logging.getLogger(__name__)

VERSION_BRANCH_PREFIX = "yaap/v"
MERGE_FOLDER_PREFIX = "_v"
# The board's own branch and the namespace YAAP keeps for its branches are never a default branch.
BOARD_BRANCH = "yaap-board"
OUTPUT_LIMIT = 4000
_COMMIT = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


class VersionBranchError(Exception):
    """The version branch cannot be made, merged or moved; the message is a sentence that is safe to show."""


@dataclass(frozen=True)
class MergeCopy:
    """The copy a card is merged in and what it started from."""

    repository: Repository
    folder: Path
    version: str
    version_branch: str
    # The version branch's commit, or the default branch's head when the version branch does not exist yet.
    start_commit: str
    # The version branch's commit in the project; None when it does not exist yet.
    existing_tip: str | None
    card_branch: str
    card_tip: str


@dataclass(frozen=True)
class MergeOutcome:
    merged: bool
    commit: str | None
    output: str


def version_branch_name(version: str) -> str:
    """`yaap/v<version>`, for a version name the board accepts and a branch name YAAP accepts."""
    name = f"{VERSION_BRANCH_PREFIX}{version}"
    if VERSION_NAME.fullmatch(version) is None or not safe_branch_name(name):
        raise VersionBranchError("This version name cannot be a branch name.")
    return name


def merge_folder_name(version: str) -> str:
    version_branch_name(version)
    return f"{MERGE_FOLDER_PREFIX}{version}"


def merge_folder(version: str) -> str:
    """The copy's folder relative to the project folder."""
    return copy_relative(merge_folder_name(version))


def may_move_version(refs: Sequence[tuple[str, str]], branch: str, commit: str) -> bool:
    """Whether a bundle's refs are exactly the version branch at the commit that was merged and checked."""
    try:
        check_build_bundle(refs, branch, commit)
    except BuildCopyError:
        return False
    return True


def merge_refusal(
    column: str,
    review_done: bool,
    version: str | None,
    plan_status: str | None,
    branch: str | None,
    default_branch: str | None = None,
) -> str | None:
    """Why the card cannot be merged into its version, or None when it can."""
    if version is None:
        return "The card has no version."
    if column == "review" and not review_done:
        return "Review the card first."
    if column not in ("review", "done"):
        return "Only a reviewed card can be merged into its version."
    if plan_status != "done":
        return "The card's build has not finished."
    if not branch or not safe_branch_name(branch):
        return "The card has no build branch."
    try:
        version_branch = version_branch_name(version)
    except VersionBranchError as error:
        return str(error)
    if branch in (version_branch, default_branch):
        return "The card's branch is the version branch or the default branch; there is nothing to merge."
    return None


def default_branch(place: Repository) -> tuple[str, str]:
    """The project's default branch and its commit: what `origin/HEAD` points at when that branch exists here, else
    main, else master, else the checked-out branch."""
    remote = run_git(["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"], place, accept_exit_codes=(0, 1))
    candidates = [remote.stdout.strip().removeprefix("refs/remotes/origin/")] if remote.exit_code == 0 else []
    candidates += ["main", "master"]
    checked_out = run_git(["symbolic-ref", "--quiet", "--short", "HEAD"], place, accept_exit_codes=(0, 1))
    if checked_out.exit_code == 0:
        candidates.append(checked_out.stdout.strip())
    for name in candidates:
        tip = branch_tip(place, name) if safe_branch_name(name) else None
        if tip is None or not _COMMIT.fullmatch(tip):
            continue
        if name == BOARD_BRANCH or name.startswith("yaap/"):
            raise VersionBranchError("The project's default branch cannot be found; versions need one.")
        return name, tip
    raise VersionBranchError("The project's default branch cannot be found; versions need one.")


def version_tip(place: Repository, version: str) -> str | None:
    return branch_tip(place, version_branch_name(version))


def merged_into(place: Repository, version: str, branch: str) -> bool:
    """Whether the branch's tip is part of the version branch. False when either does not exist."""
    tip = branch_tip(place, branch) if safe_branch_name(branch) else None
    target = version_tip(place, version)
    if tip is None or target is None:
        return False
    return run_git(["merge-base", "--is-ancestor", tip, target], place, accept_exit_codes=(0, 1)).exit_code == 0


def make_merge_copy(place: Repository, place_of: PlaceOf, version: str, card_branch: str) -> MergeCopy:
    """`.yaap/builds/_v<version>` as a repository holding the version branch (or the default branch's head as its
    start) and the card's branch. Nothing is left behind when it fails."""
    version_branch = version_branch_name(version)
    name = merge_folder_name(version)
    if not safe_branch_name(card_branch):
        raise VersionBranchError("The card's branch name cannot be used.")
    try:
        default_name, default_commit = default_branch(place)
        if card_branch in (version_branch, default_name):
            raise VersionBranchError("The card's branch is the version branch or the default branch.")
        existing = version_tip(place, version)
        card_tip = branch_tip(place, card_branch)
    except GitError as error:
        raise VersionBranchError(str(error)) from error
    if card_tip is None:
        raise VersionBranchError(f"The branch {card_branch} does not exist in the project.")
    start_commit = existing or default_commit
    start_ref = BRANCH_PREFIX + (version_branch if existing else default_name)
    card_ref = BRANCH_PREFIX + card_branch
    project_folder = folder_of(place)
    try:
        write_builds_ignore(project_folder)
        remove_build_copy(project_folder, name)
        with SafeRoot(project_folder) as root:
            root.ensure_directory(merge_folder(version))
    except (BuildCopyError, SafeRootError, OSError) as error:
        raise VersionBranchError(f"The merge folder cannot be made: {error}") from error
    folder = copy_folder(project_folder, name)
    try:
        data = bundle_create(place, [start_ref, card_ref], timeout=COPY_TIMEOUT_SECONDS)
        _, refs = bundle_header(data)
        if sorted(refs) != sorted([(start_commit, start_ref), (card_tip, card_ref)]):
            raise VersionBranchError("A branch moved while the merge was prepared; merge again.")
        repository = place_of(folder)
        run_git(["init", "--quiet", "-b", version_branch], repository)
        unbundle(repository, data, timeout=COPY_TIMEOUT_SECONDS)
        run_git(["update-ref", BRANCH_PREFIX + version_branch, start_commit, ""], repository)
        run_git(["update-ref", card_ref, card_tip, ""], repository)
        run_git(["reset", "--hard", "--quiet", "HEAD"], repository, timeout=COPY_TIMEOUT_SECONDS)
    except (GitError, BuildCopyError, VersionBranchError) as error:
        remove_merge_copy(project_folder, version)
        if isinstance(error, VersionBranchError):
            raise
        raise VersionBranchError(str(error)) from error
    return MergeCopy(repository, folder, version, version_branch, start_commit, existing, card_branch, card_tip)


def merge_card(copy: MergeCopy, message: str) -> MergeOutcome:
    """Merge the card's branch into the version branch in the copy, with a merge commit. A conflict is aborted and
    reported with the files that conflicted."""
    result = run_git(
        ["merge", "--no-ff", "--no-edit", "-m", message, BRANCH_PREFIX + copy.card_branch],
        copy.repository,
        accept_exit_codes=(0, 1),
        timeout=COPY_TIMEOUT_SECONDS,
    )
    output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
    if result.exit_code == 0:
        head = run_git(["rev-parse", "--verify", "HEAD^{commit}"], copy.repository).stdout.strip()
        return MergeOutcome(True, head, output[-OUTPUT_LIMIT:])
    conflicts = run_git(["diff", "--name-only", "--diff-filter=U"], copy.repository, accept_exit_codes=(0, 1))
    files = [line.strip() for line in conflicts.stdout.splitlines() if line.strip()]
    try:
        run_git(["merge", "--abort"], copy.repository, accept_exit_codes=(0, 1, 128))
    except GitError:
        logger.info("The merge in %s could not be aborted; the copy is removed anyway.", copy.folder)
    listed = ("\nConflicts in: " + ", ".join(files)) if files else ""
    return MergeOutcome(False, None, output[:OUTPUT_LIMIT] + listed)


def deliver_version(place: Repository, copy: MergeCopy, commit: str) -> None:
    """Move the project's version branch to the merge commit, which came back as a bundle with exactly that ref. It
    must descend from the commit the merge started from and contain the card's branch, and the branch must still be
    where the merge started (a compare-and-swap)."""
    branch = copy.version_branch
    try:
        checked_out = run_git(["symbolic-ref", "--quiet", "--short", "HEAD"], place, accept_exit_codes=(0, 1))
        if checked_out.stdout.strip() == branch:
            raise VersionBranchError("The version branch is checked out in the project; check out another branch.")
        if commit != copy.start_commit:
            data = bundle_create(
                copy.repository, [BRANCH_PREFIX + branch, f"^{copy.start_commit}"], timeout=COPY_TIMEOUT_SECONDS
            )
            if not may_move_version(bundle_header(data)[1], branch, commit):
                raise VersionBranchError("The merge's bundle holds more than the version branch; nothing was moved.")
            if not may_move_version(unbundle(place, data, timeout=COPY_TIMEOUT_SECONDS), branch, commit):
                raise VersionBranchError("The merge's bundle holds more than the version branch; nothing was moved.")
        for ancestor in (copy.start_commit, copy.card_tip):
            held = run_git(["merge-base", "--is-ancestor", ancestor, commit], place, accept_exit_codes=(0, 1))
            if held.exit_code != 0:
                raise VersionBranchError("The merge does not contain the branches it was made from; nothing moved.")
        try:
            run_git(["update-ref", BRANCH_PREFIX + branch, commit, copy.existing_tip or ""], place)
        except GitError as error:
            raise VersionBranchError("The version branch moved meanwhile; merge again.") from error
    except GitError as error:
        raise VersionBranchError(str(error)) from error


def remove_merge_copy(project_folder: Path, version: str) -> None:
    """Remove the version's merge copy; only `.yaap/builds/_v<version>`, never through a link."""
    try:
        remove_build_copy(project_folder, merge_folder_name(version))
    except BuildCopyError as error:
        raise VersionBranchError(str(error)) from error
