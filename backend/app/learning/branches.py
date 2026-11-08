"""The branch a Blueprint proposal waits on.

The proposed file is committed onto a new branch of the layer's repository with git's plumbing (a temporary index,
`commit-tree`, `update-ref`), from the commit the layer's current branch is at. Nothing is checked out: the layer's
own branch, its work tree and any uncommitted changes stay as they were, and an eval variant reads the layer at the
branch (`blueprint.at_ref`). Accepting commits the change onto the layer's current branch as one commit
(`blueprint.layers.write_file`) and removes the branch; declining only removes the branch.
"""

import tempfile
from pathlib import Path

from app.blueprint.layers import BlueprintError, Layer
from app.git.service import GitError, run_git

BRANCH_PREFIX = "yaap-learning/"


def branch_name(proposal_id: int) -> str:
    return f"{BRANCH_PREFIX}{proposal_id}"


def is_repository(layer: Layer) -> bool:
    return (layer.repository / ".git").exists()


def head_commit(repository: Path) -> str | None:
    """The commit the current branch is at; None in a repository without commits."""
    found = run_git(["rev-parse", "--verify", "--quiet", "HEAD^{commit}"], repository, accept_exit_codes=(0, 1))
    return found.stdout.strip() or None


def write_branch(layer: Layer, path: str, content: str, branch: str, message: str) -> str:
    """Commit content at path (relative to the layer) onto a new branch; returns the commit. Refuses an existing
    branch, so a proposal never overwrites another's."""
    if not branch.startswith(BRANCH_PREFIX):
        raise BlueprintError("A learning branch starts with yaap-learning/.")
    repository = layer.repository
    try:
        base = head_commit(repository)
        with tempfile.TemporaryDirectory(prefix="yaap-learning-") as folder:
            index = {"GIT_INDEX_FILE": str(Path(folder) / "index")}
            blob_file = Path(folder) / "blob"
            blob_file.write_bytes(content.replace("\r\n", "\n").encode("utf-8"))
            if base is not None:
                run_git(["read-tree", base], repository, index)
            blob = run_git(["hash-object", "-w", "--no-filters", "--", str(blob_file)], repository).stdout.strip()
            entry = f"100644,{blob},{layer.prefix}{path}"
            run_git(["update-index", "--add", "--cacheinfo", entry], repository, index)
            tree = run_git(["write-tree"], repository, index).stdout.strip()
        parents = ["-p", base] if base is not None else []
        commit = run_git(["commit-tree", tree, *parents, "-m", message], repository).stdout.strip()
        # The empty old value makes git refuse when the branch exists already.
        run_git(["update-ref", f"refs/heads/{branch}", commit, ""], repository)
    except GitError as error:
        raise BlueprintError(f"The proposal's branch could not be written: {error}") from error
    return commit


def branch_exists(layer: Layer, branch: str) -> bool:
    if not is_repository(layer):
        return False
    found = run_git(
        ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], layer.repository, accept_exit_codes=(0, 1)
    )
    return found.stdout.strip() != ""


def remove_branch(layer: Layer, branch: str) -> None:
    """Remove a learning branch; nothing to do when it is gone already."""
    if not branch.startswith(BRANCH_PREFIX) or not branch_exists(layer, branch):
        return
    try:
        run_git(["update-ref", "-d", f"refs/heads/{branch}"], layer.repository)
    except GitError as error:
        raise BlueprintError(f"The proposal's branch could not be removed: {error}") from error
