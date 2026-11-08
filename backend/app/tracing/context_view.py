"""What a model call saw, for the timeline (tracing): a snapshot's parts grouped by source, with sizes.

A step's context is listed without its content, so the timeline stays small; the content of one part is read when the
user opens it. A part whose blob was pruned is listed as gone.
"""

import json
from dataclasses import dataclass

from app.tracing.snapshots import JsonValue, ManifestEntry, SnapshotStore

# Groups in the order the model reads them: the system prompt's sources, then the tools, then the messages.
GROUP_ORDER = (
    "base",
    "agent",
    "rules",
    "standards",
    "skills",
    "project",
    "repo_map",
    "target",
    "document",
    "delegation",
    "utility",
    "other",
    "tools",
    "messages",
)
LABEL_CHARS = 80


@dataclass(frozen=True)
class ContextPart:
    key: str
    label: str
    # Characters of the content as shown; None when its blob is gone.
    chars: int | None


@dataclass(frozen=True)
class ContextGroup:
    source: str
    chars: int
    parts: list[ContextPart]


@dataclass(frozen=True)
class StepContext:
    groups: list[ContextGroup]
    chars: int
    # False when a blob it points to was pruned.
    complete: bool


def group_of(entry: ManifestEntry) -> str:
    if entry.kind == "system":
        return entry.source or "other"
    return "tools" if entry.kind == "tools" else "messages"


def group_rank(source: str) -> int:
    return GROUP_ORDER.index(source) if source in GROUP_ORDER else len(GROUP_ORDER)


def shown(entry: ManifestEntry, value: JsonValue) -> str:
    """A part's content as the timeline shows it: a system part's text, else indented JSON."""
    if entry.kind == "system" and isinstance(value, str):
        return value
    return json.dumps(value, indent=2, ensure_ascii=False)


def first_line(text: str) -> str:
    line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return line if len(line) <= LABEL_CHARS else f"{line[: LABEL_CHARS - 1]}…"


def message_label(value: JsonValue) -> str:
    """The role of a message and what it holds: its first line of text, or the tools it called."""
    if not isinstance(value, dict):
        return "message"
    role = str(value.get("role", "message"))
    content = value.get("content")
    blocks = [block for block in content if isinstance(block, dict)] if isinstance(content, list) else []
    text = " ".join(str(block.get("text", "")) for block in blocks if block.get("type") == "text").strip()
    if text:
        return f"{role}: {first_line(text)}"
    tools = [str(block.get("name")) for block in blocks if block.get("type") == "tool_call"]
    if tools:
        return f"{role}: called {', '.join(tools)}"
    return role


def part_label(entry: ManifestEntry, value: JsonValue | None) -> str:
    if value is None:
        return "gone (pruned)"
    if entry.kind == "tools":
        count = len(value) if isinstance(value, list) else 0
        return f"{count} tool definitions"
    if entry.kind == "message":
        return message_label(value)
    return first_line(value) if isinstance(value, str) else entry.source or "other"


def step_context(store: SnapshotStore, digest: str) -> StepContext | None:
    """A snapshot's parts grouped by source with their sizes; None when the snapshot itself is gone."""
    entries = store.entries(digest)
    if entries is None:
        return None
    grouped: dict[str, list[ContextPart]] = {}
    complete = True
    for entry in entries:
        value = store.get(entry.blob)
        complete = complete and value is not None
        chars = len(shown(entry, value)) if value is not None else None
        grouped.setdefault(group_of(entry), []).append(ContextPart(entry.key, part_label(entry, value), chars))
    ordered = sorted(grouped, key=group_rank)
    groups = [
        ContextGroup(source, sum(part.chars or 0 for part in grouped[source]), grouped[source]) for source in ordered
    ]
    return StepContext(groups, sum(group.chars for group in groups), complete)


def part_content(store: SnapshotStore, digest: str, key: str) -> str | None:
    """The content of one part of a snapshot; None when the snapshot, the part or its blob is gone."""
    entry = next((entry for entry in store.entries(digest) or [] if entry.key == key), None)
    if entry is None:
        return None
    value = store.get(entry.blob)
    return shown(entry, value) if value is not None else None
