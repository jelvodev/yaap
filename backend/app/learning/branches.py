"""The branch a Blueprint proposal waits on.

The proposed file is committed onto a new branch of the layer's repository with git's plumbing (a temporary index,
`commit-tree`, `update-ref`), from the commit the layer's current branch is at. Nothing is checked out: the layer's
own branch, its work tree and any uncommitted changes stay as they were, and an eval variant reads the layer at the
branch (`blueprint.at_ref`). Accepting commits the change onto the layer's current branch as one commit
(`blueprint.layers.write_file`) and removes the branch; declining only removes the branch.
"""

import re
from pathlib import Path

from app.blueprint.layers import BlueprintError, Layer
from app.git.service import GitError, Repository, folder_of, run_git

BRANCH_PREFIX = "yaap-learning/"
UNSAFE_NAME_CHARACTERS = re.compile(r"[^A-Za-z0-9]+")


def branch_name(proposal_id: int) -> str:
    return f"{BRANCH_PREFIX}{proposal_id}"


def is_repository(layer: Layer) -> bool:
    return (layer.repository / ".git").exists()


def head_commit(repository: Repository) -> str | None:
    """The commit the current branch is at; None in a repository without commits."""
    found = run_git(["rev-parse", "--verify", "--quiet", "HEAD^{commit}"], repository, accept_exit_codes=(0, 1))
    return found.stdout.strip() or None


def scratch_files(repository: Repository, branch: str) -> tuple[Path, str, Path, str]:
    """The temporary index and blob files of one branch: YAAP's path and the path relative to the repository folder.

    They live in the repository's `.git` folder, which git (possibly in the runner) sees as a relative path and YAAP
    writes to as a file, so the runner needs no access to anything outside the repository. A `.git` that is a link
    could lead YAAP's writes elsewhere, so it is refused."""
    folder = folder_of(repository)
    git_folder = folder / ".git"
    if git_folder.is_symlink() or not git_folder.is_dir():
        raise BlueprintError("The layer's .git is not a plain folder, so no branch is written in it.")
    stem = f"yaap-learning-{UNSAFE_NAME_CHARACTERS.sub('-', branch.removeprefix(BRANCH_PREFIX))}"
    return git_folder / f"{stem}.index", f".git/{stem}.index", git_folder / f"{stem}.blob", f".git/{stem}.blob"


def write_branch(layer: Layer, repository: Repository, path: str, content: str, branch: str, message: str) -> str:
    """Commit content at path (relative to the layer) onto a new branch; returns the commit. Refuses an existing
    branch, so a proposal never overwrites another's. `repository` is the layer's repository where git runs."""
    if not branch.startswith(BRANCH_PREFIX):
        raise BlueprintError("A learning branch starts with yaap-learning/.")
    index_file, index_name, blob_file, blob_name = scratch_files(repository, branch)
    try:
        for stale in (index_file, blob_file):
            stale.unlink(missing_ok=True)
        base = head_commit(repository)
        index = {"GIT_INDEX_FILE": index_name}
        # Exclusive creation does not follow a link left at the path.
        with blob_file.open("xb") as handle:
            handle.write(content.replace("\r\n", "\n").encode("utf-8"))
        if base is not None:
            run_git(["read-tree", base], repository, index)
        blob = run_git(["hash-object", "-w", "--no-filters", "--", blob_name], repository).stdout.strip()
        entry = f"100644,{blob},{layer.prefix}{path}"
        run_git(["update-index", "--add", "--cacheinfo", entry], repository, index)
        tree = run_git(["write-tree"], repository, index).stdout.strip()
        parents = ["-p", base] if base is not None else []
        commit = run_git(["commit-tree", tree, *parents, "-m", message], repository).stdout.strip()
        # The empty old value makes git refuse when the branch exists already.
        run_git(["update-ref", f"refs/heads/{branch}", commit, ""], repository)
    except GitError as error:
        raise BlueprintError(f"The proposal's branch could not be written: {error}") from error
    except OSError as error:
        raise BlueprintError(f"The proposal's branch could not be written: {error}") from error
    finally:
        for scratch in (index_file, blob_file):
            scratch.unlink(missing_ok=True)
    return commit


def branch_exists(layer: Layer, repository: Repository, branch: str) -> bool:
    if not is_repository(layer):
        return False
    found = run_git(
        ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], repository, accept_exit_codes=(0, 1)
    )
    return found.stdout.strip() != ""


def remove_branch(layer: Layer, repository: Repository, branch: str) -> None:
    """Remove a learning branch; nothing to do when it is gone already."""
    if not branch.startswith(BRANCH_PREFIX):
        return
    try:
        if not branch_exists(layer, repository, branch):
            return
        run_git(["update-ref", "-d", f"refs/heads/{branch}"], repository)
    except GitError as error:
        raise BlueprintError(f"The proposal's branch could not be removed: {error}") from error
