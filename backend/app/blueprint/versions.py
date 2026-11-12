"""The version of each Blueprint layer: the git commit it was at, and whether it had uncommitted changes.

A run records this per layer when it starts, so a replay can read each layer as it was then (replays/blueprint.py).
A layer outside a git repository has no commit; the copies that replays and eval variants read at a ref are not
repositories either.
"""

import logging
from dataclasses import dataclass
from typing import Any, Literal

from app.blueprint.layers import BlueprintError, Layer, LayerKind
from app.blueprint.mutations import layer_place
from app.git.service import GitError, run_git
from app.targets.base import TargetError

logger = logging.getLogger(__name__)

# Where a replay's layer came from: the original run's version, the variant's ref, or the layer as it is now.
LayerSource = Literal["original", "variant", "current"]

STATUS_TIMEOUT_SECONDS = 5
LAYER_KINDS: tuple[LayerKind, ...] = ("global", "environment", "project")
HEADER = "# "
BRANCH_OID = "# branch.oid "
NO_COMMIT_YET = "(initial)"


@dataclass(frozen=True)
class LayerVersion:
    kind: LayerKind
    commit: str | None
    dirty: bool

    def as_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "commit": self.commit, "dirty": self.dirty}

    @staticmethod
    def from_json(value: object) -> "LayerVersion | None":
        """None for an entry that is not a layer version."""
        if not isinstance(value, dict):
            return None
        kind, commit, dirty = value.get("kind"), value.get("commit"), value.get("dirty")
        known = next((candidate for candidate in LAYER_KINDS if candidate == kind), None)
        if known is None or not (commit is None or isinstance(commit, str)) or not isinstance(dirty, bool):
            return None
        return LayerVersion(known, commit or None, dirty)


def versions_to_json(versions: list[LayerVersion]) -> list[dict[str, Any]]:
    return [version.as_json() for version in versions]


def versions_from_json(value: object) -> list[LayerVersion]:
    """The stored versions; entries that are missing or invalid are skipped."""
    if not isinstance(value, list):
        return []
    found = (LayerVersion.from_json(entry) for entry in value)
    return [version for version in found if version is not None]


def parse_layer_status(output: str) -> tuple[str | None, bool]:
    """The commit and the dirty flag from `git status --porcelain=v2 --branch`: the commit is the `# branch.oid`
    header (none before the first commit), and any line other than a header is a change."""
    commit: str | None = None
    dirty = False
    for line in output.splitlines():
        if line.startswith(BRANCH_OID):
            oid = line[len(BRANCH_OID) :].strip()
            commit = None if oid in ("", NO_COMMIT_YET) else oid
        elif line.strip() and not line.startswith(HEADER):
            dirty = True
    return commit, dirty


def layer_version(layer: Layer) -> LayerVersion:
    """The layer's commit and whether it has changes. One git call, without optional locks so it never blocks or
    races a writer. Synchronous: call it from a worker thread."""
    if not (layer.repository / ".git").exists():
        return LayerVersion(layer.kind, None, False)
    pathspec = layer.prefix.rstrip("/") or "."
    result = run_git(
        ["--no-optional-locks", "status", "--porcelain=v2", "--branch", "--untracked-files=normal", "--", pathspec],
        layer_place(layer),
        timeout=STATUS_TIMEOUT_SECONDS,
    )
    commit, dirty = parse_layer_status(result.stdout)
    return LayerVersion(layer.kind, commit, dirty)


def layer_versions(layers: list[Layer]) -> list[LayerVersion]:
    """The version of each layer; a layer whose version cannot be read has no commit."""
    found: list[LayerVersion] = []
    for layer in layers:
        try:
            found.append(layer_version(layer))
        except (GitError, BlueprintError, TargetError, OSError, TimeoutError) as error:
            logger.warning("The version of the %s Blueprint layer could not be read: %s", layer.kind, error)
            found.append(LayerVersion(layer.kind, None, False))
    return found
