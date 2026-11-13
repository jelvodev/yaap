"""The built-in command /check (choice 14): check a project against its merged standards.

`/check` goes through every active standard and engineering standard of the conversation's layers, in the order the
Blueprint merges them; `/check <name>` checks one. The run is read only; the report has a fixed form so that it can be
read and compared: per standard a verdict and the deviations with file and line.
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any

from app.blueprint.layers import Item, Layer, active_items

CHECK_COMMAND = re.compile(r"^/check(?:\s+(\S.*))?$", re.DOTALL)
CHECKED_KINDS = ("standard", "engineering")
CHECK_INSTRUCTION_PREFIX = "Check this project against the standards below, one at a time, in this order."


def is_read_only_check(blocks: Sequence[Mapping[str, object]]) -> bool:
    """Recognize persisted built-in read-only commands (/check, /plan), including checks saved before the explicit
    marker existed, so trying such a message again stays read only."""
    for block in blocks:
        if block.get("type") != "command":
            continue
        if block.get("read_only") is True:
            return True
        text = block.get("text")
        if block.get("name") == "check" and isinstance(text, str) and text.startswith(CHECK_INSTRUCTION_PREFIX):
            return True
    return False


class CheckError(Exception):
    """A /check that cannot run; the message is safe to show."""


def standards_to_check(layers: list[Layer], name: str | None = None) -> list[Item]:
    items = [item for item in active_items(layers) if item.kind in CHECKED_KINDS]
    if name is None:
        if not items:
            raise CheckError("This project has no standards to check. Add them in the Blueprint first.")
        return items
    chosen = [item for item in items if item.name == name]
    if not chosen:
        available = ", ".join(item.name for item in items) or "none"
        raise CheckError(f"There is no standard named {name}. Standards here: {available}.")
    return chosen


def check_block(layers: list[Layer], text: str) -> dict[str, Any] | None:
    """The command block for a `/check` message, or None when the message is not one."""
    match = CHECK_COMMAND.match(text.strip())
    if match is None:
        return None
    name = (match.group(1) or "").strip() or None
    items = standards_to_check(layers, name)
    listed = "\n".join(f"{number}. {item.name} ({item.kind}, from {item.source.label})" for number, item in enumerate(items, 1))
    instructions = (
        f"{CHECK_INSTRUCTION_PREFIX} For each, load its full text "
        "with load_standard, then look at the project's files that it concerns: list, search and read them. You work "
        "read only: change nothing, run no commands, only read.\n\n"
        f"Standards to check:\n{listed}\n\n"
        "Report in exactly this form, in the reply language, one section per standard in the same order:\n\n"
        "## <standard name>\n"
        "**Verdict:** follows | deviates | not applicable\n"
        "<one or two sentences on why>\n"
        "- `<path>:<line>` <what deviates, and what the standard asks instead>\n\n"
        "List deviations only under deviates, each with its file and line; give no list for follows or not applicable. "
        "Base every verdict on files you read, and say so when you could only check a sample. End with a short "
        "## Summary: how many standards follow, deviate and do not apply, and the three deviations most worth fixing "
        "first."
    )
    return {"type": "command", "name": "check", "text": instructions, "read_only": True}
