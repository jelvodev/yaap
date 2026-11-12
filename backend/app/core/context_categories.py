"""The categories of a conversation's context: mapping, splitting and scaling, pure and without database access.

The parts of a model request (system parts by source, tool definitions, messages) are sized with the division of
`app.tracing.division` and put into categories. The sizes are estimates; `scale_to_total` makes them add up to the
context the last model call really used.
"""

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, get_args

from app.agent.tool_catalog import LISTING_HEADING
from app.core.summaries import SUMMARY_HEADING
from app.providers.base import ToolDefinition
from app.tracing.context_view import first_line
from app.tracing.division import definition_chars, estimate_tokens, tool_group

ContextCategoryKey = Literal[
    "system_base",
    "system_project",
    "system_repo_map",
    "system_mcp_servers",
    "system_other",
    "blueprint_rules",
    "blueprint_standards",
    "blueprint_skills",
    "tools_yaap",
    "tools_joined",
    "messages_user",
    "messages_answers",
    "messages_thinking",
    "messages_tool_calls",
    "messages_tool_results",
    "messages_summary",
    "messages_attachments",
    "cli_remainder",
    "buffer",
    "free",
]
ContextGroupKey = Literal["system", "blueprint", "tools", "messages", "cli", "space"]

CATEGORY_ORDER: tuple[ContextCategoryKey, ...] = get_args(ContextCategoryKey)
CATEGORY_LABELS: dict[ContextCategoryKey, str] = {
    "system_base": "Base prompt",
    "system_project": "Project and memory",
    "system_repo_map": "Repository map",
    "system_mcp_servers": "MCP server list",
    "system_other": "Other system parts",
    "blueprint_rules": "Rules",
    "blueprint_standards": "Standards",
    "blueprint_skills": "Skills, commands and agents",
    "tools_yaap": "YAAP's tools",
    "tools_joined": "Joined tools",
    "messages_user": "Your messages",
    "messages_answers": "Answers",
    "messages_thinking": "Thinking",
    "messages_tool_calls": "Tool calls",
    "messages_tool_results": "Tool results",
    "messages_summary": "Summary of earlier messages",
    "messages_attachments": "Attachments",
    "cli_remainder": "The CLI's own prompt and history",
    "buffer": "Buffer until YAAP compacts",
    "free": "Free",
}
GROUP_OF: dict[str, ContextGroupKey] = {
    "system": "system",
    "blueprint": "blueprint",
    "tools": "tools",
    "messages": "messages",
    "cli": "cli",
    "buffer": "space",
    "free": "space",
}
# System part sources with a category of their own; the others are system_other (or the MCP server list).
SOURCE_CATEGORY: dict[str, ContextCategoryKey] = {
    "base": "system_base",
    "project": "system_project",
    "repo_map": "system_repo_map",
    "rules": "blueprint_rules",
    "standards": "blueprint_standards",
    "skills": "blueprint_skills",
}
SOURCE_LABELS = {
    "base": "Base prompt",
    "agent": "Agent role",
    "rules": "General instructions",
    "standards": "Standards",
    "skills": "Skills, commands and agents",
    "project": "Project and memory",
    "repo_map": "Repository map",
    "target": "Target description",
    "document": "Shared document",
    "delegation": "Delegation task",
    "utility": "Utility prompt",
    "other": "Other",
}
MAX_ITEMS = 8
# Images and documents are counted by the provider, not by their text; a rough figure each.
ATTACHMENT_TOKENS = 1500
ATTACHMENT_TYPES = frozenset({"attachment", "image", "document"})
THINKING_TYPES = frozenset({"reasoning", "thinking"})
SUMMARY_LABEL_CHARS = 60
# Input fields that tell best what a tool call was about, in order of preference.
SUMMARY_FIELDS = ("path", "file_path", "command", "query", "pattern", "url", "name", "task")


@dataclass(frozen=True)
class ContextItem:
    label: str
    tokens: int
    step_id: int | None = None
    # For a tool definition: where its tool comes from ("yaap", "browser", "mcp:<server>"), so a consumer need not
    # read it from the label.
    tool_group: str | None = None
    # The run of that step, which a link into the trace needs besides the step.
    run_id: int | None = None


@dataclass(frozen=True)
class StepRef:
    run_id: int
    step_id: int


Entry = tuple[ContextCategoryKey, ContextItem]


@dataclass(frozen=True)
class ContextCategory:
    key: ContextCategoryKey
    group: ContextGroupKey
    label: str
    tokens: int
    # The largest items first, at most MAX_ITEMS.
    items: list[ContextItem] = field(default_factory=list)


@dataclass(frozen=True)
class Draft:
    """A category with estimated sizes, before scaling."""

    key: ContextCategoryKey
    items: list[ContextItem]

    @property
    def tokens(self) -> int:
        return sum(item.tokens for item in self.items)


def group_of(key: ContextCategoryKey) -> ContextGroupKey:
    return GROUP_OF[key.split("_", 1)[0]]


def system_category(source: str, text: str) -> ContextCategoryKey:
    """The category of a system part: its source, and for an unnamed part whether it is the MCP server list."""
    if source in SOURCE_CATEGORY:
        return SOURCE_CATEGORY[source]
    return "system_mcp_servers" if text.startswith(LISTING_HEADING) else "system_other"


def scale_to_total(estimates: list[int], total: int) -> list[int]:
    """The estimates scaled so that they add up to exactly total (largest-remainder rounding).

    Estimates that are all zero cannot be scaled and stay zero.
    """
    whole = sum(estimates)
    if total <= 0 or whole <= 0:
        return [0] * len(estimates)
    shares = [divmod(estimate * total, whole) for estimate in estimates]
    scaled = [quotient for quotient, _ in shares]
    by_remainder = sorted(range(len(estimates)), key=lambda index: (-shares[index][1], index))
    for index in by_remainder[: total - sum(scaled)]:
        scaled[index] += 1
    return scaled


def group_drafts(entries: Sequence[Entry]) -> list[Draft]:
    grouped: dict[ContextCategoryKey, list[ContextItem]] = defaultdict(list)
    for key, item in entries:
        if item.tokens > 0:
            grouped[key].append(item)
    return [Draft(key, grouped[key]) for key in CATEGORY_ORDER if key in grouped]


def split_system(parts: Sequence[tuple[str, str]]) -> list[Draft]:
    """System parts as (source, text)."""
    entries: list[Entry] = []
    for source, text in parts:
        key = system_category(source, text)
        label = "MCP servers" if key == "system_mcp_servers" else SOURCE_LABELS.get(source, "Other")
        entries.append((key, ContextItem(label, estimate_tokens(len(text)))))
    return group_drafts(entries)


def split_tools(definitions: Sequence[ToolDefinition]) -> list[Draft]:
    """YAAP's tools one item each; the browser and each MCP server one item for all of its tools."""
    entries: list[Entry] = []
    joined: dict[str, int] = defaultdict(int)
    for definition in definitions:
        group = tool_group(definition.name)
        if group == "yaap":
            size = estimate_tokens(definition_chars(definition))
            entries.append(("tools_yaap", ContextItem(definition.name, size, tool_group=group)))
        else:
            joined[group] += definition_chars(definition)
    for group, chars in joined.items():
        label = "browser" if group == "browser" else f"MCP: {group.removeprefix('mcp:')}"
        entries.append(("tools_joined", ContextItem(label, estimate_tokens(chars), tool_group=group)))
    return group_drafts(entries)


def input_summary(tool_input: Any) -> str:
    """A short phrase for what a tool call was about: its path, command or query."""
    if not isinstance(tool_input, dict):
        return ""
    value = next((tool_input[name] for name in SUMMARY_FIELDS if isinstance(tool_input.get(name), str)), None)
    if value is None:
        value = next((item for item in tool_input.values() if isinstance(item, str)), "")
    return first_line(value)[:SUMMARY_LABEL_CHARS]


def dump(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)


def tool_entries(block: Mapping[str, Any], links: Mapping[str, StepRef]) -> list[Entry]:
    name = str(block.get("name") or "tool")
    label = f"{name} {input_summary(block.get('input'))}".strip()
    call_id = block.get("id")
    ref = links.get(call_id) if isinstance(call_id, str) else None
    step_id, run_id = (ref.step_id, ref.run_id) if ref else (None, None)
    call = estimate_tokens(len(name) + len(dump(block.get("input"))))
    output = estimate_tokens(len(dump(block.get("output") or "")))
    return [
        ("messages_tool_calls", ContextItem(label, call, step_id, run_id=run_id)),
        ("messages_tool_results", ContextItem(label, output, step_id, run_id=run_id)),
    ]


def block_entries(role: str, block: Mapping[str, Any], links: Mapping[str, StepRef]) -> list[Entry]:
    """The categories one stored content block counts for."""
    kind = block.get("type")
    text = str(block.get("text") or block.get("thinking") or "")
    tokens = estimate_tokens(len(text))
    if kind in ATTACHMENT_TYPES:
        return [("messages_attachments", ContextItem(str(kind), ATTACHMENT_TOKENS))]
    if kind == "summary" or (kind == "text" and role == "user" and text.startswith(SUMMARY_HEADING)):
        return [("messages_summary", ContextItem("summary", tokens))]
    if kind in THINKING_TYPES:
        return [("messages_thinking", ContextItem(f"thinking: {first_line(text)}", tokens))]
    if kind == "tool_call":
        return tool_entries(block, links)
    if role == "user":
        return [("messages_user", ContextItem(f"user: {first_line(text)}", tokens))]
    return [("messages_answers", ContextItem(f"answer: {first_line(text)}", tokens))]


def split_messages(
    messages: Sequence[tuple[str, Sequence[Mapping[str, Any]]]], links: Mapping[str, StepRef]
) -> list[Draft]:
    """Messages as (role, stored content blocks); links maps a tool call's id to its step in the trace."""
    entries = [entry for role, blocks in messages for block in blocks for entry in block_entries(role, block, links)]
    return group_drafts(entries)


def scaled_items(items: list[ContextItem], factor: float) -> list[ContextItem]:
    shown = [
        ContextItem(item.label, round(item.tokens * factor), item.step_id, item.tool_group, item.run_id)
        for item in items
    ]
    return sorted(shown, key=lambda item: -item.tokens)[:MAX_ITEMS]


def finish_categories(drafts: list[Draft], total: int, scale: bool) -> list[ContextCategory]:
    """Categories with their items. With scale the tokens add up to total; without, they stay the estimates."""
    drafts = sorted(drafts, key=lambda draft: CATEGORY_ORDER.index(draft.key))
    estimates = [draft.tokens for draft in drafts]
    tokens = scale_to_total(estimates, total) if scale and sum(estimates) > 0 else estimates
    categories = []
    for draft, estimate, size in zip(drafts, estimates, tokens, strict=True):
        factor = size / estimate if estimate else 1.0
        items = scaled_items(draft.items, factor)
        label = CATEGORY_LABELS[draft.key]
        categories.append(ContextCategory(draft.key, group_of(draft.key), label, size, items))
    return categories


def remainder_category(total: int, categories: Sequence[ContextCategory]) -> ContextCategory:
    """What a CLI connection holds beyond YAAP's own parts: its base prompt and the history it keeps."""
    rest = max(0, total - sum(category.tokens for category in categories))
    return ContextCategory("cli_remainder", "cli", CATEGORY_LABELS["cli_remainder"], rest)


def space_categories(total: int, limit: int | None, compact_at: int | None) -> list[ContextCategory]:
    """The buffer up to the point where YAAP compacts, and the free space up to the context limit."""
    spaces = []
    if compact_at is not None:
        spaces.append(ContextCategory("buffer", "space", CATEGORY_LABELS["buffer"], max(0, compact_at - total)))
    if limit is not None:
        free = max(0, limit - max(total, compact_at or total))
        spaces.append(ContextCategory("free", "space", CATEGORY_LABELS["free"], free))
    return spaces
