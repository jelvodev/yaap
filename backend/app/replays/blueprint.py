"""The Blueprint a replay builds: each layer at the commit the original run had, the variant's layer at its ref.

A layer with no recorded commit (a run from before commits were recorded, a layer that is not a git repository) or
whose commit can no longer be read is used as it is now.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.blueprint.at_ref import layer_at_ref
from app.blueprint.layers import BlueprintError, Layer, LayerKind
from app.blueprint.versions import LAYER_KINDS, LayerSource, LayerVersion
from app.evals.variants import Variant, variant_layers

logger = logging.getLogger(__name__)

SOURCES: tuple[LayerSource, ...] = ("original", "variant", "current")


@dataclass(frozen=True)
class ReplayLayer:
    """What the replay built for one layer: the commit it was read at, whether the layer had uncommitted changes then,
    and where the version came from (a variant names its ref instead of a commit)."""

    kind: LayerKind
    commit: str | None
    dirty: bool
    source: LayerSource
    ref: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "commit": self.commit, "dirty": self.dirty, "source": self.source, "ref": self.ref}

    @staticmethod
    def from_json(value: object) -> "ReplayLayer | None":
        """None for an entry that is not a replay layer."""
        if not isinstance(value, dict):
            return None
        kind = next((known for known in LAYER_KINDS if known == value.get("kind")), None)
        source = next((known for known in SOURCES if known == value.get("source")), None)
        commit, ref, dirty = value.get("commit"), value.get("ref"), value.get("dirty")
        if kind is None or source is None or not isinstance(dirty, bool):
            return None
        return ReplayLayer(
            kind, commit if isinstance(commit, str) else None, dirty, source, ref if isinstance(ref, str) else None
        )


def replay_layers_from_json(value: object) -> list[ReplayLayer]:
    """The stored layers of a replay; empty for one stored before they were kept, entries that are invalid skipped."""
    if not isinstance(value, list):
        return []
    found = (ReplayLayer.from_json(entry) for entry in value)
    return [layer for layer in found if layer is not None]


def build_replay_layers(
    layers: list[Layer], variant: Variant, original: list[LayerVersion], folder: Path
) -> tuple[list[Layer], list[ReplayLayer]]:
    """The layers for the replay, and what each was built from. The variant's layer is read at its ref; the others at
    the commit the original run recorded. Synchronous git: call it from a worker thread. Raises VariantError."""
    with_variant = variant_layers(layers, variant, folder)
    recorded = {version.kind: version for version in original}
    built: list[Layer] = []
    described: list[ReplayLayer] = []
    for layer, planned in zip(layers, with_variant, strict=True):
        if variant.layer == layer.kind and variant.ref is not None:
            built.append(planned)
            described.append(ReplayLayer(layer.kind, None, False, "variant", variant.ref))
            continue
        version = recorded.get(layer.kind)
        at_commit = layer_at_commit(layer, version, folder) if version is not None else None
        built.append(at_commit if at_commit is not None else layer)
        described.append(
            ReplayLayer(layer.kind, version.commit, version.dirty, "original")
            if at_commit is not None and version is not None
            else ReplayLayer(layer.kind, None, False, "current")
        )
    return built, described


def layer_at_commit(layer: Layer, version: LayerVersion, folder: Path) -> Layer | None:
    """The layer at the recorded commit; None when there is none or it cannot be read any more."""
    if version.commit is None:
        return None
    try:
        return layer_at_ref(layer, version.commit, folder / layer.kind)
    except BlueprintError as error:
        logger.info("The %s Blueprint layer cannot be read at %s any more: %s", layer.kind, version.commit, error)
        return None
