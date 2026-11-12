"""Recognise the files of other tools' setups (Claude Code, Codex, Cursor, Copilot) by their path alone.

Nothing here reads a file or touches the disk: a path goes in and a decision comes out. Settings, hooks, MCP and
auth files are refused by name so the importer never opens them.
"""

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

from app.blueprint.layers import ItemKind

Tool = Literal["claude_code", "codex", "cursor", "copilot"]
Layout = Tool
Action = Literal["item", "skill_file", "refuse", "skip", "unknown"]

MARKERS: dict[str, Tool] = {
    ".claude": "claude_code",
    ".codex": "codex",
    ".agents": "codex",
    ".cursor": "cursor",
    ".github": "copilot",
}
FOLDER_OF_LAYOUT: dict[Layout, str] = {
    "claude_code": ".claude",
    "codex": ".codex",
    "cursor": ".cursor",
    "copilot": ".github",
}
ROOT_INSTRUCTIONS: dict[str, Tool] = {
    "CLAUDE.md": "claude_code",
    "AGENTS.md": "codex",
    "AGENTS.override.md": "codex",
    ".cursorrules": "cursor",
}
ROOT_REFUSED = (
    ".mcp.json",
    ".claude.json",
    "mcp.json",
    "settings.json",
    "settings.local.json",
    "config.toml",
    "auth.json",
)
# An upload may sit in one wrapper folder (a zipped project), but no deeper: a CLAUDE.md in a skill folder is
# that skill's file.
ROOT_DEPTH = 2
PRIVATE_NOTE = "CLAUDE.local.md is a private file of its author; check that it is meant to be shared."
SECRET_FILE_REASON = "Settings, hooks and credentials hold commands or secrets; YAAP never reads them."
MARKDOWN = (".md", ".markdown")


@dataclass(frozen=True)
class Recognition:
    action: Action
    kind: ItemKind | None = None
    # The item name; for a skill file, the name of the skill it belongs to.
    name: str = ""
    tool: Tool | None = None
    # Why a file is refused.
    reason: str = ""
    notes: tuple[str, ...] = ()
    # For a skill file: its path inside the skill folder.
    inner: str = ""
    # The source format, which decides how the front matter is read: "mdc" or "instructions" for standards.
    format: str = ""


SKIP = Recognition("skip")


def refuse(tool: Tool | None = None) -> Recognition:
    return Recognition("refuse", tool=tool, reason=SECRET_FILE_REASON)


def item(kind: ItemKind, name: str, tool: Tool, notes: tuple[str, ...] = (), format: str = "") -> Recognition:
    return Recognition("item", kind, name, tool, notes=notes, format=format)


def nested_name(parts: tuple[str, ...], suffix: str) -> str:
    """Subfolders joined with the file name, so frontend/x.md becomes frontend-x."""
    *folders, file = parts
    return "-".join([*folders, file[: -len(suffix)]])


def markdown_name(parts: tuple[str, ...]) -> str | None:
    last = parts[-1].lower()
    suffix = next((suffix for suffix in MARKDOWN if last.endswith(suffix)), None)
    return nested_name(parts, suffix) if suffix else None


def skills(parts: tuple[str, ...], tool: Tool) -> Recognition:
    """skills/<name>/SKILL.md is the skill; any other file in skills/<name>/ belongs to it."""
    if len(parts) < 3:
        return SKIP
    if len(parts) == 3 and parts[2] == "SKILL.md":
        return item("skill", parts[1], tool)
    return Recognition("skill_file", "skill", parts[1], tool, inner="/".join(parts[2:]))


def claude_code(parts: tuple[str, ...]) -> Recognition:
    first = parts[0]
    if parts == ("CLAUDE.md",):
        return item("instructions", "instructions", "claude_code")
    if first == "skills":
        return skills(parts, "claude_code")
    if first == "agents" and len(parts) > 1 and (name := markdown_name(parts[1:])):
        return item("agent", name, "claude_code")
    if first == "commands" and len(parts) > 1 and (name := markdown_name(parts[1:])):
        return item("command", name, "claude_code")
    if first == "hooks" or parts[-1].lower().endswith(".json"):
        return refuse("claude_code")
    return SKIP


def codex(parts: tuple[str, ...], folder: str) -> Recognition:
    first = parts[0]
    if first == "skills":
        return skills(parts, "codex")
    if folder == ".agents":
        return SKIP
    if parts in (("AGENTS.md",), ("AGENTS.override.md",)):
        return item("instructions", "instructions", "codex")
    if first == "prompts" and len(parts) == 2 and (name := markdown_name(parts[1:])):
        return item("command", name, "codex")
    if parts[-1].lower().endswith((".json", ".toml")):
        return refuse("codex")
    return SKIP


def cursor(parts: tuple[str, ...]) -> Recognition:
    first = parts[0]
    if first == "rules" and len(parts) > 1 and parts[-1].lower().endswith(".mdc"):
        return item("standard", nested_name(parts[1:], ".mdc"), "cursor", format="mdc")
    if first == "commands" and len(parts) == 2 and (name := markdown_name(parts[1:])):
        return item("command", name, "cursor")
    if parts[-1].lower().endswith(".json"):
        return refuse("cursor")
    return SKIP


def copilot(parts: tuple[str, ...]) -> Recognition:
    first = parts[0]
    last = parts[-1].lower()
    if parts == ("copilot-instructions.md",):
        return item("instructions", "instructions", "copilot")
    if first == "instructions" and len(parts) > 1 and last.endswith(".instructions.md"):
        return item("standard", nested_name(parts[1:], ".instructions.md"), "copilot", format="instructions")
    if first == "prompts" and len(parts) > 1 and last.endswith(".prompt.md"):
        return item("command", nested_name(parts[1:], ".prompt.md"), "copilot")
    return SKIP


def inside_tool_folder(folder: str, parts: tuple[str, ...]) -> Recognition:
    if not parts:
        return SKIP
    tool = MARKERS[folder]
    if tool == "claude_code":
        return claude_code(parts)
    if tool == "cursor":
        return cursor(parts)
    if tool == "copilot":
        return copilot(parts)
    return codex(parts, folder)


def at_root(parts: tuple[str, ...]) -> Recognition | None:
    """Files that sit beside the tool folders: CLAUDE.md, AGENTS.md, .cursorrules, .mcp.json."""
    name = parts[-1]
    if len(parts) > ROOT_DEPTH:
        return None
    if name in ROOT_INSTRUCTIONS:
        return item("instructions", "instructions", ROOT_INSTRUCTIONS[name])
    if name == "CLAUDE.local.md":
        return item("instructions", "instructions", "claude_code", notes=(PRIVATE_NOTE,))
    if name in ROOT_REFUSED:
        return refuse()
    return None


def recognise(path: str, layout: Layout | None = None) -> Recognition:
    """What a file of a setup is, by its path. `layout` says the upload's root is that tool's own folder."""
    parts = PurePosixPath(path.replace("\\", "/")).parts
    if not parts:
        return SKIP
    folders = parts[:-1]
    marker = max((index for index, folder in enumerate(folders) if folder in MARKERS), default=None)
    if marker is not None:
        return inside_tool_folder(folders[marker], parts[marker + 1 :])
    if layout is not None:
        found = inside_tool_folder(FOLDER_OF_LAYOUT[layout], parts)
        if found.action != "skip":
            return found
    found = at_root(parts)
    if found is not None:
        return found
    if layout is None and parts[-1].lower().endswith(MARKDOWN):
        return Recognition("unknown")
    return SKIP
