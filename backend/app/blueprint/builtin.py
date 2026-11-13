"""YAAP's own helper prompts, shipped as Blueprint files in app/builtin_blueprint.

The built-in layer is read-only and never part of the layers the agent sees. A prompt that may be changed is read
through instructions(), which prefers the same file in the user's layers (nearest wins, like any Blueprint item);
prompts that decide on security always come from the shipped file.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Literal, get_args

from app.blueprint.layers import BlueprintError, Layer, active_items, parse_document

BuiltinName = Literal[
    "explorer",
    "worker",
    "brief-worker",
    "reviewer",
    "router",
    "guard",
    "summariser",
    "memory-keeper",
    "learner",
    "planner",
    "state-keeper",
    "specification",
]
BuiltinKind = Literal["agent", "skill"]
BuiltinRole = Literal["worker", "explorer"]

BUILTIN_DIR = Path(__file__).resolve().parent.parent / "builtin_blueprint"
KIND_BY_NAME: dict[str, BuiltinKind] = {
    "explorer": "agent",
    "worker": "agent",
    "brief-worker": "agent",
    "reviewer": "agent",
    "router": "agent",
    "guard": "agent",
    "summariser": "skill",
    "memory-keeper": "skill",
    "learner": "skill",
    "planner": "agent",
    "state-keeper": "skill",
    "specification": "skill",
}
# Names whose behaviour is a security decision: a user file of the same name never replaces them.
LOCKED = frozenset({"guard", "outgoing-check"})
SECTION_HEADING = re.compile(r"^## (.*?)\s*$")


@dataclass(frozen=True)
class BuiltinItem:
    name: BuiltinName
    kind: BuiltinKind
    path: str
    description: str
    role: BuiltinRole | None
    tasks: tuple[str, ...]
    when: str
    body: str


@dataclass(frozen=True)
class OverrideMatch:
    layer: Layer
    body: str


def builtin_layer() -> Layer:
    return Layer("builtin", "Built-in", BUILTIN_DIR, BUILTIN_DIR)


def item_path(name: str, kind: BuiltinKind) -> str:
    return f"skills/{name}/SKILL.md" if kind == "skill" else f"agents/{name}.md"


def trim(text: str) -> str:
    return text.strip("\n")


def load_item(name: BuiltinName) -> BuiltinItem | None:
    kind = KIND_BY_NAME[name]
    path = item_path(name, kind)
    try:
        text = (BUILTIN_DIR / path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    document = parse_document(text)
    front = document.front_matter
    role = front.get("role")
    tasks = front.get("tasks")
    return BuiltinItem(
        name=name,
        kind=kind,
        path=path,
        description=str(front.get("description") or "").strip(),
        role="worker" if role == "worker" else "explorer" if role == "explorer" else None,
        tasks=tuple(str(task) for task in tasks) if isinstance(tasks, list) else (),
        when=str(front.get("when") or "").strip(),
        body=trim(document.body),
    )


@cache
def builtin_items() -> dict[BuiltinName, BuiltinItem]:
    """The shipped items by name; an item whose file is absent is left out."""
    found = (load_item(name) for name in get_args(BuiltinName))
    return {item.name: item for item in found if item is not None}


def can_override(name: str) -> bool:
    return name in KIND_BY_NAME and name not in LOCKED


def section_of(body: str, section: str) -> str | None:
    """The text under a `## <section>` heading, up to the next `## ` heading, or None when there is none."""
    collected: list[str] | None = None
    for line in body.split("\n"):
        heading = SECTION_HEADING.match(line)
        if heading is not None:
            if collected is not None:
                break
            if heading.group(1) == section:
                collected = []
            continue
        if collected is not None:
            collected.append(line)
    return trim("\n".join(collected)) if collected is not None else None


def pick(body: str, section: str | None) -> str | None:
    return trim(body) if section is None else section_of(body, section)


def override_match(name: str, layers: Sequence[Layer]) -> OverrideMatch | None:
    kind = KIND_BY_NAME.get(name)
    if kind is None or not can_override(name):
        return None
    path = item_path(name, kind)
    for item in active_items(list(layers)):
        if item.path == path:
            return OverrideMatch(item.source, item.document.body)
    return None


def overridden_by(name: str, layers: Sequence[Layer]) -> Layer | None:
    match = override_match(name, layers)
    return match.layer if match is not None else None


def instructions(name: BuiltinName, layers: Sequence[Layer], section: str | None = None) -> str:
    """The text of a built-in item: the user's replacement when the item may be replaced and one is active, else the
    shipped text. With a section, only that part; a replacement without it falls back to the shipped section."""
    shipped = builtin_items().get(name)
    if shipped is None:
        raise BlueprintError(f"The built-in item {name} is not shipped.")
    match = override_match(name, layers)
    if match is not None:
        chosen = pick(match.body, section)
        if chosen is not None:
            return chosen
    return pick(shipped.body, section) or ""


def shipped_instructions(name: BuiltinName, section: str | None = None) -> str:
    """The text of a built-in item from the shipped file only, for prompts that must not be replaced."""
    return instructions(name, [], section)


def render(template: str, values: Mapping[str, str]) -> str:
    """Fill `{key}` for the given keys only; every other brace stays as written."""
    if not values:
        return template
    pattern = re.compile("|".join("{" + re.escape(key) + "}" for key in values))
    return pattern.sub(lambda found: values[found.group(0)[1:-1]], template)
