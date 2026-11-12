"""Tools that join a run while it works (efficiency): which of a run's extra tools are held back until the
model asks for them, the search over an outside MCP server's tools, and the list of servers in the prompt.

A run's extra tools (the browser, outside MCP servers) are all known when it starts, but not all are offered: the
browser's tools wait for `open_browser`, and the tools of a server that is not fully listed wait for `find_tools`.
The catalog is fixed for the run; what has joined is in the run's state, so the list a provider reads before each
model call grows from the next call on.
"""

import re
from dataclasses import dataclass

from app.core.prompts import after_cacheable
from app.providers.base import SystemPart

# A server with at most this many tools is fully listed unless its setting says otherwise.
SMALL_SERVER_TOOLS = 5
MAX_MATCHES = 6
MAX_LISTED_NAMES = 40
SHORT_DESCRIPTION = 160
WORD = re.compile(r"[a-z0-9]+")
CAMEL = re.compile(r"([a-z0-9])([A-Z])")
STOP_WORDS = frozenset({"a", "an", "and", "the", "to", "of", "for", "in", "on", "or", "with", "by", "from", "tool"})


@dataclass(frozen=True)
class ServerListing:
    """An outside MCP server as the prompt lists it."""

    name: str
    description: str
    # Its tools, by the name the model sees (<server>__<tool>).
    tools: tuple[str, ...]
    # Every tool offered from the start; otherwise they wait for find_tools.
    listed: bool


@dataclass(frozen=True)
class ToolCatalog:
    """The run's extra tools that are offered only after they join, and its outside MCP servers."""

    # The browser's tools, by the name the model sees; they join together through open_browser.
    browser: frozenset[str] = frozenset()
    servers: tuple[ServerListing, ...] = ()

    @property
    def searchable(self) -> frozenset[str]:
        """The tools of servers that are not fully listed: they join through find_tools."""
        return frozenset(name for server in self.servers if not server.listed for name in server.tools)

    @property
    def deferred(self) -> frozenset[str]:
        return self.browser | self.searchable


def lists_all(setting: bool | None, tool_count: int) -> bool:
    """Whether a server's tools are all offered from the start: its setting, or else whether it is small."""
    return setting if setting is not None else tool_count <= SMALL_SERVER_TOOLS


def words(text: str) -> list[str]:
    return [word for word in WORD.findall(CAMEL.sub(r"\1 \2", text).lower()) if word not in STOP_WORDS]


def short_name(name: str) -> str:
    """A tool's own name, without its server: `create_issue` for `github__create_issue`."""
    return name.split("__", 1)[1] if "__" in name else name


def short_description(description: str) -> str:
    """The first sentence of a tool's description, without the server prefix, at most SHORT_DESCRIPTION characters."""
    text = re.sub(r"^\[MCP server [^\]]*\]\s*", "", description).strip()
    sentence = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    return sentence if len(sentence) <= SHORT_DESCRIPTION else sentence[: SHORT_DESCRIPTION - 1].rstrip() + "…"


def score(query: list[str], name: str, description: str) -> int:
    """How well a tool matches the query's words: a word of its name counts most, the start of one less, a word of
    its description least."""
    name_words = set(words(short_name(name)))
    description_words = set(words(description))
    total = 0
    for word in query:
        if word in name_words:
            total += 3
        elif any(len(word) >= 3 and (part.startswith(word) or word.startswith(part)) for part in name_words):
            total += 2
        elif word in description_words or (len(word) >= 3 and any(part.startswith(word) for part in description_words)):
            total += 1
    return total


def best_matches(query: str, candidates: dict[str, str], limit: int = MAX_MATCHES) -> list[str]:
    """The names of the candidates (name -> description) that match the query best, best first."""
    wanted = words(query)
    scored = [(score(wanted, name, description), name) for name, description in candidates.items()]
    return [name for points, name in sorted(scored, key=lambda item: (-item[0], item[1])) if points > 0][:limit]


def server_line(server: ServerListing) -> str:
    count = f"{len(server.tools)} tool{'' if len(server.tools) == 1 else 's'}"
    line = f"- {server.name} ({count}{'' if server.listed else ', find_tools'})"
    if server.description:
        line += f": {server.description.strip()}"
    if not server.listed:
        names = [short_name(name) for name in server.tools]
        shown = ", ".join(names[:MAX_LISTED_NAMES])
        more = f" and {len(names) - MAX_LISTED_NAMES} more" if len(names) > MAX_LISTED_NAMES else ""
        line += f" Tools: {shown}{more}."
    return line


LISTING_HEADING = "## MCP servers"
# A server line of the listing whose tools wait for find_tools: "- name (12 tools, find_tools)".
SEARCHABLE_LINE = re.compile(r"^- .+? \((\d+) tools?, find_tools\)", re.MULTILINE)


def searchable_tool_count(listing: str) -> int:
    """How many tools a rendered listing holds back for find_tools."""
    return sum(int(count) for count in SEARCHABLE_LINE.findall(listing))


def listing_part(catalog: ToolCatalog) -> SystemPart | None:
    """The run's outside MCP servers for the system prompt; cacheable, since it changes only with the settings."""
    if not catalog.servers:
        return None
    lines = [
        LISTING_HEADING,
        "",
        "Outside MCP servers of this conversation; their tools are named <server>__<tool>. When a server's tools are "
        "not among your tools (marked find_tools), call find_tools with what you need, and the matching tools are "
        "added for your next step.",
        *(server_line(server) for server in catalog.servers),
    ]
    return SystemPart(text="\n".join(lines), cacheable=True)


def with_listing(system: list[SystemPart], catalog: ToolCatalog) -> list[SystemPart]:
    """The system prompt with the server list right after its last cacheable part, so it is cached with them."""
    return after_cacheable(system, listing_part(catalog))
