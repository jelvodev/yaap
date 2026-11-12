"""Turn the front matter of another tool's agent, command, rule or skill into what YAAP writes.

Pure functions. Only mapped keys go into `extra`, never the source's front matter as it is: a model id, a preset or
a hook list from an untrusted file must not reach the Blueprint by accident. Everything that is not carried over is
named in the notes so the user sees it in the review.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from app.blueprint.layers import ItemKind

TOOL_NAMES = {
    "read": "read_file",
    "write": "write_file",
    "edit": "edit_file",
    "multiedit": "edit_file",
    "bash": "run_command",
    "glob": "list_files",
    "ls": "list_files",
    "grep": "search_project",
    "webfetch": "web_fetch",
    "websearch": "web_search",
    "task": "delegate",
    "agent": "delegate",
    "skill": "load_skill",
}
WORKER_MODELS = ("haiku", "sonnet")
CODE_FIELDS = ("hooks", "mcpServers", "mcp_servers")
CODE_NOTE = "not imported: it holds commands"
AGENT_HANDLED = ("name", "description", "tools", "model", "skills", *CODE_FIELDS)
COMMAND_DROPPED = ("argument-hint", "allowed-tools", "model")
PLACEHOLDERS = (
    re.compile(r"\$ARGUMENTS\[\d+\]"),
    re.compile(r"\$\{input:[^}]*\}"),
    re.compile(r"\$[0-9](?![0-9A-Za-z_])"),
    re.compile(r"\$(?!ARGUMENTS\b)[A-Z][A-Z0-9_]+\b"),
)


@dataclass(frozen=True)
class Mapped:
    summary: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    notes: tuple[str, ...] = ()


def listed(value: Any) -> list[str]:
    """A comma-separated string or a list, as a list of trimmed strings."""
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        return []
    return [str(part).strip() for part in value if str(part).strip()]


def text(front: dict[str, Any], key: str) -> str:
    value = front.get(key)
    return str(value).strip() if isinstance(value, (str, int, float)) else ""


def named(keys: list[str], what: str) -> str:
    return f"{', '.join(sorted(keys))}: {what}"


def map_tools(value: Any) -> tuple[list[str], list[str]]:
    """The YAAP tool names for a source tool list, and the source names that have no YAAP tool."""
    mapped: list[str] = []
    unknown: list[str] = []
    for tool in listed(value):
        # Claude Code writes Bash(git status:*); the permission part is not a tool name.
        base = tool.split("(", 1)[0].strip()
        target = TOOL_NAMES.get(base.lower())
        if target is None:
            unknown.append(tool)
        elif target not in mapped:
            mapped.append(target)
    return mapped, unknown


def map_agent(front: dict[str, Any]) -> Mapped:
    extra: dict[str, Any] = {}
    notes: list[str] = []
    if "tools" in front:
        tools, unknown = map_tools(front["tools"])
        if tools:
            extra["tools"] = tools
        if unknown:
            notes.append(f"Tools without a YAAP tool were left out: {', '.join(unknown)}.")
    model = text(front, "model").lower()
    if model in WORKER_MODELS:
        extra["role"] = "worker"
    elif model:
        notes.append(f"model: {text(front, 'model')} is not imported; choose a preset for this agent in YAAP.")
    skills = listed(front.get("skills"))
    if skills:
        extra["skills"] = skills
    held = [key for key in CODE_FIELDS if key in front]
    if held:
        notes.append(named(held, CODE_NOTE) + ".")
    dropped = [key for key in front if key not in AGENT_HANDLED]
    if dropped:
        notes.append(named(dropped, "dropped, YAAP has no such setting") + ".")
    return Mapped(text(front, "description"), extra, tuple(notes))


def unfilled_placeholders(body: str) -> list[str]:
    found: list[str] = []
    for pattern in PLACEHOLDERS:
        for match in pattern.finditer(body):
            if match.group(0) not in found:
                found.append(match.group(0))
    return found


def map_command(front: dict[str, Any], body: str) -> Mapped:
    notes: list[str] = []
    placeholders = unfilled_placeholders(body)
    if placeholders:
        notes.append(f"{', '.join(placeholders)}: YAAP does not fill this; only $ARGUMENTS is replaced.")
    dropped = [key for key in COMMAND_DROPPED if key in front]
    dropped += [key for key in front if key not in ("description", *COMMAND_DROPPED, "name")]
    if dropped:
        notes.append(named(dropped, "dropped, YAAP commands only keep the description") + ".")
    return Mapped(text(front, "description"), {}, tuple(notes))


def map_standard(front: dict[str, Any], format: str) -> Mapped:
    notes: list[str] = []
    scoping = ("alwaysApply", "globs") if format == "mdc" else ("applyTo",)
    present = [key for key in scoping if key in front]
    for key in present:
        shown = str(front[key])[:200]
        notes.append(f"{key}: {shown}. The Blueprint has no per-file rules; this standard applies everywhere.")
    return Mapped(text(front, "description"), {}, tuple(notes))


def map_skill(front: dict[str, Any]) -> Mapped:
    notes: list[str] = []
    if "hooks" in front:
        notes.append(named(["hooks"], CODE_NOTE) + ".")
    dropped = [key for key in front if key not in ("name", "description", "hooks")]
    if dropped:
        notes.append(named(dropped, "dropped, YAAP skills only keep the description") + ".")
    return Mapped(text(front, "description"), {}, tuple(notes))


def string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def extra_key_valid(key: str, value: Any, known_tools: frozenset[str] | None) -> bool:
    if key == "tools":
        return string_list(value) and (known_tools is None or set(value) <= known_tools)
    if key == "role":
        return value == "worker"
    return key == "skills" and string_list(value)


def clean_extra(
    kind: ItemKind, extra: dict[str, Any], known_tools: frozenset[str] | None = None
) -> tuple[dict[str, Any], list[str]]:
    """The front matter YAAP may write for an item, and the keys that are refused.

    Only an agent has any: its tools (YAAP tool names), the worker role and its skills. Anything else, a model id,
    a preset or a hook list included, must come from the user in YAAP, not from a file or a browser.
    """
    if kind != "agent":
        return {}, sorted(extra)
    kept = {key: value for key, value in extra.items() if extra_key_valid(key, value, known_tools)}
    return kept, sorted(set(extra) - set(kept))


def map_item(kind: ItemKind, format: str, front: dict[str, Any], body: str) -> Mapped:
    if kind == "agent":
        return map_agent(front)
    if kind == "command":
        return map_command(front, body)
    if kind == "standard":
        return map_standard(front, format)
    if kind == "skill":
        return map_skill(front)
    return Mapped()
