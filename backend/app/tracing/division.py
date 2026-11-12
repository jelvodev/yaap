"""How a model request is divided into parts and sized, shared by the token footprint and the context breakdown.

Each part has a size in characters and estimated tokens: the system parts by source, the tool definitions grouped by
where they come from (YAAP's own, the browser, each outside MCP server) and the messages. One division means the
footprint script and the screen never count differently.

Tokens are estimated as characters / 4, rounded up: the common rule of thumb for English text and code with the
tokenizers of the vendors YAAP supports; no tokenizer is available offline without a new dependency. The estimate is
the same before and after a change, so the difference is what counts. A tool definition is measured as the compact
JSON of its name, description and input schema, which is what every vendor's API carries.
"""

import json
import math
from dataclasses import dataclass
from typing import Literal

from app.providers.base import AgentRequest, ContentBlock, ToolDefinition

CHARS_PER_TOKEN = 4
ESTIMATOR = "characters / 4, rounded up"
BROWSER_PREFIX = "browser__"
MCP_SEPARATOR = "__"

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
