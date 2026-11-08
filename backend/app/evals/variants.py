"""What an eval run changes from the project's normal setup.

A variant can play the cases with another preset, another agent, or one Blueprint layer at another git ref (a branch
with a proposed rule, say); a replay can also take another model. The layer is read at that ref without checking it
out (`blueprint.at_ref`), so the layer's own branch and files stay untouched.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from app.blueprint.at_ref import check_ref, layer_at_ref
from app.blueprint.layers import BlueprintError, Layer, LayerKind

LAYER_KINDS: tuple[LayerKind, ...] = ("global", "environment", "project")


class VariantError(Exception):
    """A variant that cannot be used; the message is safe to show."""


@dataclass(frozen=True)
class Variant:
    preset: str | None = None
    agent: str | None = None
    # One Blueprint layer read at a git ref; both or neither.
    layer: LayerKind | None = None
    ref: str | None = None
    # Another model than the preset's (a replay); eval runs leave it empty.
    model: str | None = None

    @property
    def empty(self) -> bool:
        return self == Variant()

    @property
    def label(self) -> str:
        parts = []
        if self.preset:
            parts.append(f"preset {self.preset}")
        if self.agent:
            parts.append(f"agent {self.agent}")
        if self.model:
            parts.append(f"model {self.model}")
        if self.layer and self.ref:
            parts.append(f"{self.layer} Blueprint at {self.ref}")
        return ", ".join(parts) or "normal setup"

    def as_json(self) -> dict[str, Any] | None:
        if self.empty:
            return None
        value: dict[str, Any] = {"preset": self.preset, "agent": self.agent, "layer": self.layer, "ref": self.ref}
        if self.model:
            value["model"] = self.model
        return value


def checked_variant(
    preset: str | None, agent: str | None, layer: str | None, ref: str | None, model: str | None = None
) -> Variant:
    """A variant from user input, or VariantError."""
    preset, agent, ref = (preset or "").strip() or None, (agent or "").strip() or None, (ref or "").strip() or None
    if (layer is None) != (ref is None):
        raise VariantError("A Blueprint variant needs both the layer and the git ref.")
    if layer is not None and layer not in LAYER_KINDS:
        raise VariantError("The layer is global, environment or project.")
    if ref is not None:
        try:
            ref = check_ref(ref)
        except BlueprintError as error:
            raise VariantError(str(error)) from error
    return Variant(preset, agent, cast(LayerKind | None, layer), ref, (model or "").strip() or None)


def stored_variant(value: dict[str, Any] | None) -> Variant:
    """The variant of a run as stored; nothing stored is the normal setup."""
    if not value:
        return Variant()
    layer = value.get("layer")
    return Variant(
        preset=value.get("preset") or None,
        agent=value.get("agent") or None,
        layer=cast(LayerKind, layer) if layer in LAYER_KINDS else None,
        ref=value.get("ref") or None,
        model=value.get("model") or None,
    )


def variant_layers(layers: list[Layer], variant: Variant, folder: Path) -> list[Layer]:
    """The layers with the variant's layer read at its ref into folder; the same layers without one."""
    if variant.layer is None or variant.ref is None:
        return layers
    kind: Literal["global", "environment", "project"] = variant.layer
    index = next((index for index, layer in enumerate(layers) if layer.kind == kind), None)
    if index is None:
        raise VariantError(f"This project has no {kind} Blueprint layer.")
    try:
        replaced = layer_at_ref(layers[index], variant.ref, folder / kind)
    except BlueprintError as error:
        raise VariantError(str(error)) from error
    return [*layers[:index], replaced, *layers[index + 1 :]]
