"""The token footprint: what YAAP sends to a model, measured without a model.

A footprint is a list of situations (a plain chat, a project conversation, a goal turn); each has parts with their
size in characters and estimated tokens: the system parts by source, the tool definitions grouped by where they come
from (YAAP's own, the browser, each outside MCP server), the user message, and the first tool results of a fixed
sequence of calls. A change that should send less compares a footprint before and after.

Tokens are estimated as characters / 4, rounded up: the common rule of thumb for English text and code with the
tokenizers of the vendors YAAP supports; no tokenizer is available offline without a new dependency. The estimate is
the same before and after a change, so the difference is what counts. A tool definition is measured as the compact
JSON of its name, description and input schema, which is what every vendor's API carries.
"""

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from app.core.turn_context import HEADING as TURN_CONTEXT_HEADING
from app.providers.base import AgentRequest, ContentBlock, ToolDefinition

CHARS_PER_TOKEN = 4
ESTIMATOR = "characters / 4, rounded up"
BROWSER_PREFIX = "browser__"
MCP_SEPARATOR = "__"
# The date and time in the turn context change with every message; they are replaced by a fixed text so two
# measurements of the same code give the same numbers.
DATE_LINE = re.compile(r"Date and time: [^\n]*")
FIXED_DATE_LINE = "Date and time: Sunday 8 November 2026, 12:00 (UTC)."

PartGroup = Literal["system", "tools", "message", "tool_result"]


def estimate_tokens(chars: int) -> int:
    return math.ceil(chars / CHARS_PER_TOKEN)


@dataclass(frozen=True)
class Part:
    group: PartGroup
    name: str
    chars: int
    # How many items the part holds: tool definitions in a tool group, 1 otherwise.
    count: int = 1

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.chars)


@dataclass(frozen=True)
class Situation:
    name: str
    description: str
    parts: tuple[Part, ...]
    # Every offered tool with the size of its definition, for the items that shorten single tools.
    tools: tuple[tuple[str, int], ...] = ()

    def chars(self, group: PartGroup | None = None) -> int:
        return sum(part.chars for part in self.parts if group is None or part.group == group)

    def tokens(self, group: PartGroup | None = None) -> int:
        return estimate_tokens(self.chars(group))


@dataclass(frozen=True)
class Footprint:
    situations: tuple[Situation, ...]
    notes: tuple[str, ...] = field(default=())


def definition_chars(definition: ToolDefinition) -> int:
    body = {"name": definition.name, "description": definition.description, "input_schema": definition.input_schema}
    return len(json.dumps(body, ensure_ascii=False, separators=(",", ":")))


def tool_group(name: str) -> str:
    """Where a tool comes from: YAAP's own, the browser, or the outside MCP server its name starts with."""
    if name.startswith(BROWSER_PREFIX):
        return "browser"
    if MCP_SEPARATOR in name:
        return f"mcp:{name.split(MCP_SEPARATOR, 1)[0]}"
    return "yaap"


def tool_parts(definitions: list[ToolDefinition]) -> list[Part]:
    """One part per origin, YAAP's own first, then the browser, then the MCP servers by name."""
    sizes: dict[str, list[int]] = {}
    for definition in definitions:
        sizes.setdefault(tool_group(definition.name), []).append(definition_chars(definition))
    order = sorted(sizes, key=lambda group: (group != "yaap", group != "browser", group))
    return [Part("tools", f"tools:{group}", sum(sizes[group]), len(sizes[group])) for group in order]


def system_parts(request: AgentRequest) -> list[Part]:
    """One part per system part, named by its source; a source that occurs twice gets a number."""
    seen: dict[str, int] = {}
    parts = []
    for system_part in request.system:
        seen[system_part.source] = seen.get(system_part.source, 0) + 1
        suffix = f"#{seen[system_part.source]}" if seen[system_part.source] > 1 else ""
        parts.append(Part("system", f"system:{system_part.source}{suffix}", len(system_part.text)))
    return parts


def block_text(block: ContentBlock) -> str:
    text = block.get("text")
    return text if isinstance(text, str) else json.dumps(block, ensure_ascii=False, sort_keys=True)


def message_parts(request: AgentRequest) -> list[Part]:
    """The newest user message: what the user sent (with a command's or goal's text), and the turn context."""
    if not request.messages or request.messages[-1].role != "user":
        return []
    message, context = 0, 0
    for block in request.messages[-1].content:
        text = block_text(block)
        if text.startswith(TURN_CONTEXT_HEADING):
            context += len(DATE_LINE.sub(FIXED_DATE_LINE, text))
        else:
            message += len(text)
    earlier = sum(len(block_text(block)) for item in request.messages[:-1] for block in item.content)
    parts = [Part("message", "message:user", message), Part("message", "message:turn_context", context)]
    if earlier:
        parts.insert(0, Part("message", "message:history", earlier))
    return parts


def request_parts(request: AgentRequest) -> tuple[list[Part], tuple[tuple[str, int], ...]]:
    """The parts of a model request, and every tool it offers with its size."""
    definitions = request.tool_box.definitions if request.tool_box is not None else []
    tools = tuple((definition.name, definition_chars(definition)) for definition in definitions)
    return [*system_parts(request), *tool_parts(definitions), *message_parts(request)], tools


def result_part(index: int, tool: str, text: str) -> Part:
    return Part("tool_result", f"result:{index}:{tool}", len(text))


def to_json(footprint: Footprint) -> dict[str, Any]:
    return {
        "estimator": ESTIMATOR,
        "notes": list(footprint.notes),
        "situations": [
            {
                "name": situation.name,
                "description": situation.description,
                "totals": {
                    group: {"chars": situation.chars(group), "tokens": situation.tokens(group)}
                    for group in ("system", "tools", "message", "tool_result")
                }
                | {"all": {"chars": situation.chars(), "tokens": situation.tokens()}},
                "parts": [
                    {"group": p.group, "name": p.name, "count": p.count, "chars": p.chars, "tokens": p.tokens}
                    for p in situation.parts
                ],
                "tools": [{"name": name, "chars": chars} for name, chars in situation.tools],
            }
            for situation in footprint.situations
        ],
    }


def render_table(footprint: Footprint) -> str:
    """A short table per situation: each part with its count, characters and estimated tokens, then the totals."""
    lines = [f"Token footprint (tokens estimated as {ESTIMATOR})"]
    for situation in footprint.situations:
        header = f"  {'part':<34} {'n':>4} {'chars':>9} {'tokens':>8}"
        lines += ["", f"{situation.name}: {situation.description}", header]
        for part in situation.parts:
            lines.append(f"  {part.name:<34} {part.count:>4} {part.chars:>9,} {part.tokens:>8,}")
        for group in ("system", "tools", "message", "tool_result"):
            if situation.chars(group):
                lines.append(f"  {'= ' + group:<34} {'':>4} {situation.chars(group):>9,} {situation.tokens(group):>8,}")
        lines.append(f"  {'= all':<34} {'':>4} {situation.chars():>9,} {situation.tokens():>8,}")
    return "\n".join(lines) + "\n"
