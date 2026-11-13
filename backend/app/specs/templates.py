"""The specification templates: one per kind of work, shipped with the built-in skill `specification`.

The skill's instructions and its templates may both be replaced in any Blueprint layer: a file at the same path
(`skills/specification/SKILL.md`, `skills/specification/templates/<kind>.md`) wins, nearest layer first.
"""

from collections.abc import Sequence
from typing import Literal, get_args

from app.blueprint.builtin import BUILTIN_DIR, BuiltinName, instructions
from app.blueprint.layers import MAX_FILE_BYTES, Layer, layer_has_file, read_layer_text

SpecKind = Literal["feature", "bug", "refactor", "chore"]
SPEC_KINDS: tuple[SpecKind, ...] = get_args(SpecKind)
SKILL_NAME: BuiltinName = "specification"


def template_path(kind: SpecKind) -> str:
    return f"skills/{SKILL_NAME}/templates/{kind}.md"


def shipped_template(kind: SpecKind) -> str:
    return (BUILTIN_DIR / template_path(kind)).read_text(encoding="utf-8").strip("\n")


def template(kind: SpecKind, layers: Sequence[Layer]) -> str:
    """The template for a kind of work: the nearest layer's replacement when it has one that can be read, else the
    shipped one."""
    path = template_path(kind)
    for layer in reversed(layers):
        if not layer_has_file(layer, path):
            continue
        try:
            text = read_layer_text(layer, path)
        except (OSError, UnicodeDecodeError):
            continue
        if text.strip() and len(text) <= MAX_FILE_BYTES:
            return text.strip("\r\n")
    return shipped_template(kind)


def specification_instructions(kind: SpecKind, layers: Sequence[Layer]) -> str:
    """The skill's instructions followed by the template to fill, for a conversation that writes a specification."""
    return f"{instructions(SKILL_NAME, layers)}\n\n## Template ({kind})\n\n{template(kind, layers)}"
