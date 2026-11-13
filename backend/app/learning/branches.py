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
from app.targets.safe_root import SafeRoot

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


def git_folder(repository: Repository) -> Path:
    """The repository's `.git` folder as YAAP sees it; a `.git` that is a link or a file is refused."""
    folder = folder_of(repository) / ".git"
    if folder.is_symlink() or not folder.is_dir():
        raise BlueprintError("The layer's .git is not a plain folder, so no branch is written in it.")
    return folder


def scratch_names(branch: str) -> tuple[str, str]:
    """The temporary index and blob files of one branch, by their name in the repository's `.git` folder.

    Git (possibly in the runner) sees them as `.git/<name>`, so the runner needs no access to anything outside the
    repository. YAAP writes and removes them only through a SafeRoot pinned on `.git`, so a `.git` swapped for a link
    after the check cannot lead those writes elsewhere."""
    stem = f"yaap-learning-{UNSAFE_NAME_CHARACTERS.sub('-', branch.removeprefix(BRANCH_PREFIX))}"
    return f"{stem}.index", f"{stem}.blob"


def write_branch(layer: Layer, repository: Repository, path: str, content: str, branch: str, message: str) -> str:
    """Commit content at path (relative to the layer) onto a new branch; returns the commit. Refuses an existing
    branch, so a proposal never overwrites another's. `repository` is the layer's repository where git runs."""
    if not branch.startswith(BRANCH_PREFIX):
        raise BlueprintError("A learning branch starts with yaap-learning/.")
    index_name, blob_name = scratch_names(branch)
    try:
        scratch = SafeRoot(git_folder(repository))
    except OSError as error:
        raise BlueprintError(f"The proposal's branch could not be written: {error}") from error
    try:
        for stale in (index_name, blob_name):
            scratch.remove(stale)
        base = head_commit(repository)
        index = {"GIT_INDEX_FILE": f".git/{index_name}"}
        scratch.write_bytes(blob_name, content.replace("\r\n", "\n").encode("utf-8"))
        if base is not None:
            run_git(["read-tree", base], repository, index)
        blob = run_git(["hash-object", "-w", "--no-filters", "--", f".git/{blob_name}"], repository).stdout.strip()
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
        try:
            for name in (index_name, blob_name):
                scratch.remove(name)
        except OSError:
            pass  # A scratch file left in .git is harmless and replaced on the next write; the result stands.
        finally:
            scratch.close()
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
