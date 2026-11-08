"""Commands and pinned skills in user messages.

A message that starts with /name runs the command: the conversation keeps what the user typed, the model receives
the command's template filled in. A pinned skill is added once, as its full text, to the next message after it was
pinned; unpinning adds a note that it no longer applies.
"""

import re
from typing import Any

from app.blueprint.layers import Item, Layer, active_items

COMMAND_PATTERN = re.compile(r"^/([A-Za-z0-9][\w-]*)(?:[ \t]+(.*))?$", re.DOTALL)
ARGUMENTS = "$ARGUMENTS"


class MessagePartError(Exception):
    """A command or skill that cannot be used; the message says why."""


def active_of_kind(layers: list[Layer], kind: str) -> dict[str, Item]:
    return {item.name: item for item in active_items(layers) if item.kind == kind}


def expand_command(
    layers: list[Layer], text: str, allowed: tuple[str, ...] | None = None
) -> dict[str, Any] | None:
    """The command block for a message that starts with a known /command, or None for ordinary text."""
    match = COMMAND_PATTERN.match(text.strip())
    if match is None:
        return None
    name, arguments = match.group(1), (match.group(2) or "").strip()
    item = active_of_kind(layers, "command").get(name)
    if item is None or (allowed is not None and name not in allowed):
        return None
    template = item.document.body.strip()
    if ARGUMENTS in template:
        expanded = template.replace(ARGUMENTS, arguments)
    else:
        expanded = f"{template}\n\n{arguments}".strip() if arguments else template
    return {"type": "command", "name": name, "text": expanded}


def skill_text(layers: list[Layer], name: str) -> str:
    item = active_of_kind(layers, "skill").get(name)
    if item is None:
        raise MessagePartError(f"There is no skill named {name}.")
    return item.document.body.strip()


def check_skills(layers: list[Layer], names: list[str]) -> list[str]:
    available = active_of_kind(layers, "skill")
    cleaned = list(dict.fromkeys(name.strip() for name in names if name.strip()))
    unknown = [name for name in cleaned if name not in available]
    if unknown:
        raise MessagePartError(f"Unknown skill: {', '.join(unknown)}.")
    return cleaned


def delivered_skills(history: list[list[dict[str, Any]]]) -> set[str]:
    """The skills whose text the model has, judged from the skill blocks in earlier user messages."""
    delivered: set[str] = set()
    for content in history:
        for block in content:
            if block.get("type") == "skill":
                delivered.add(block["name"])
            elif block.get("type") == "skill_removed":
                delivered.discard(block["name"])
    return delivered


def skill_blocks(layers: list[Layer], pinned: list[str], history: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Blocks for the next message: skills pinned since the last message, and notes for skills unpinned."""
    delivered = delivered_skills(history)
    blocks: list[dict[str, Any]] = []
    for name in pinned:
        if name not in delivered:
            try:
                blocks.append({"type": "skill", "name": name, "text": skill_text(layers, name)})
            except MessagePartError:
                continue
    blocks += [{"type": "skill_removed", "name": name} for name in sorted(delivered - set(pinned))]
    return blocks


def model_content(content: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A stored user message as model input: commands filled in, skills as their full text."""
    has_command = any(block.get("type") in ("command", "goal") for block in content)
    converted: list[dict[str, Any]] = []
    for block in content:
        kind = block.get("type")
        if kind == "text" and has_command:
            continue
        if kind in ("command", "goal"):
            converted.append({"type": "text", "text": block["text"]})
        elif kind == "skill":
            converted.append(
                {"type": "text", "text": f'<skill name="{block["name"]}">\n{block["text"]}\n</skill>'}
            )
        elif kind == "skill_removed":
            converted.append({"type": "text", "text": f"[The skill {block['name']} no longer applies from here on.]"})
        else:
            converted.append(block)
    return converted
