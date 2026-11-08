"""A Blueprint layer as it is at a git ref, such as a branch with a proposed rule (eval variants).

The layer's files at the ref are read from its repository's object store (`git archive`) into a folder of their own,
which then serves as the layer's root. Nothing is checked out, so the branch the layer is on, its work tree and its
uncommitted changes stay exactly as they were.
"""

import re
from pathlib import Path

from app.blueprint.layers import BlueprintError, Layer
from app.git.service import GitError, archive, extract_archive, run_git

# A branch, tag or commit; never an option, a range or a reflog expression.
REF = re.compile(r"^(?!-)(?!.*\.\.)(?!.*@\{)[A-Za-z0-9._/-]{1,200}$")


def check_ref(ref: str) -> str:
    ref = ref.strip()
    if not REF.match(ref):
        raise BlueprintError(f"'{ref}' is not a branch, tag or commit name.")
    return ref


def resolve_ref(repository: Path, ref: str) -> str:
    """The commit a ref names in the layer's repository."""
    ref = check_ref(ref)
    if not (repository / ".git").exists():
        raise BlueprintError("This Blueprint layer is not a git repository, so it has no other versions.")
    try:
        found = run_git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], repository, accept_exit_codes=(0, 1))
    except GitError as error:
        raise BlueprintError(f"The ref {ref} cannot be read: {error}") from error
    commit = found.stdout.strip()
    if not commit:
        raise BlueprintError(f"The layer's repository has no branch, tag or commit {ref}.")
    return commit


def layer_at_ref(layer: Layer, ref: str, folder: Path) -> Layer:
    """A copy of the layer at ref in folder (made empty first is up to the caller); the returned layer reads from it.
    A layer that did not exist yet at that ref is empty."""
    commit = resolve_ref(layer.repository, ref)
    prefix = layer.prefix.rstrip("/")
    try:
        listed = run_git(["ls-tree", "--name-only", commit, "--", prefix], layer.repository) if prefix else None
        if listed is None or listed.stdout.strip():
            extract_archive(archive(layer.repository, commit, [prefix] if prefix else []), folder)
    except GitError as error:
        raise BlueprintError(f"The layer at {ref} cannot be read: {error}") from error
    root = folder / prefix if prefix else folder
    root.mkdir(parents=True, exist_ok=True)
    return Layer(layer.kind, f"{layer.label} at {ref}", root, folder, layer.prefix)
