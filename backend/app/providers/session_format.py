"""How a coding tool's session files are read, as the provider spec carries it, and the neutral turn model they
become. A vendor's format lives in its own package under providers/; the import service only asks this protocol."""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

# The tool ids stored in a conversation's imported_from; a new vendor format adds its id here.
SessionTool = Literal["claude_code", "codex"]
# As stored for YAAP's own tool calls (core/chat.py).
MAX_TOOL_OUTPUT = 20_000
MAX_TEXT = 200_000


class SessionImportError(Exception):
    """The file cannot be imported; the message says why."""


@dataclass
class Turn:
    """What the user typed and what the agent did after it, until the user typed again."""

    user_text: str
    blocks: list[dict[str, Any]] = field(default_factory=list)
    at: datetime | None = None
    reply_at: datetime | None = None


@dataclass
class ImportedSession:
    tool: SessionTool
    session_id: str | None
    title: str | None
    started_at: datetime | None
    cwd: str | None
    turns: list[Turn]

    @property
    def tool_calls(self) -> int:
        return sum(1 for turn in self.turns for block in turn.blocks if block["type"] == "tool_call")


class SessionFormat(Protocol):
    """One tool's session files: where they are, how to recognise a line of one, and how to read it."""

    @property
    def tool(self) -> SessionTool:
        """The id stored in imported_from.tool."""
        ...

    @property
    def label(self) -> str:
        """The tool's name for people, e.g. "Claude Code"."""
        ...

    @property
    def folder(self) -> str:
        """Where the sessions are, relative to the home folder on a target."""
        ...

    @property
    def file_pattern(self) -> re.Pattern[str]:
        """Matches a session file's path relative to the home folder."""
        ...

    @property
    def per_folder(self) -> bool:
        """Whether the sessions sit one folder down (a folder per working folder); otherwise they are searched for
        in all subfolders."""
        ...

    def matches(self, line: dict[str, Any]) -> bool:
        """Whether this JSON line shows the file to be in this format."""
        ...

    def parse(self, lines: list[dict[str, Any]]) -> ImportedSession:
        """The session in the file's lines."""
        ...


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def clip(text: str, limit: int = MAX_TOOL_OUTPUT) -> str:
    return text if len(text) <= limit else text[:limit] + "\n[Output shortened for storage.]"


def tool_call(call_id: str, name: str, arguments: Any) -> dict[str, Any]:
    tool_input = arguments if isinstance(arguments, dict) else {"input": arguments}
    # The result is filled in when it comes; a call without one keeps an empty output (never "running").
    return {
        "type": "tool_call",
        "id": call_id,
        "name": name or "tool",
        "input": tool_input,
        "output": "",
        "is_error": False,
        "imported": True,
    }


def content_text(content: Any) -> str:
    """The text of a message or tool result: a string, or a list of blocks of which the text ones count."""
    if isinstance(content, str):
        return content
    parts = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
        elif block.get("type") in ("image", "document"):
            parts.append(f"[{block['type']}]")
    return "\n".join(part for part in parts if part)


class TurnBuilder:
    def __init__(self) -> None:
        self.turns: list[Turn] = []
        self.calls: dict[str, dict[str, Any]] = {}

    def user(self, text: str, at: datetime | None) -> None:
        text = text.strip()
        if not text:
            return
        if self.turns and not self.turns[-1].blocks:
            # Two messages in a row without an answer between them: one turn.
            self.turns[-1].user_text = f"{self.turns[-1].user_text}\n\n{text}"[:MAX_TEXT]
            return
        self.turns.append(Turn(text[:MAX_TEXT], at=at))

    def current(self, at: datetime | None) -> Turn:
        if not self.turns:
            # The agent spoke first (a resumed session): the turn has no text from the user.
            self.turns.append(Turn("", at=at))
        turn = self.turns[-1]
        turn.reply_at = at or turn.reply_at
        return turn

    def text(self, kind: str, text: str, at: datetime | None) -> None:
        if not text.strip():
            return
        blocks = self.current(at).blocks
        if blocks and blocks[-1]["type"] == kind:
            blocks[-1]["text"] = f"{blocks[-1]['text']}\n\n{text.strip()}"
        else:
            blocks.append({"type": kind, "text": text.strip()})

    def call(self, call_id: str, name: str, arguments: Any, at: datetime | None) -> None:
        if call_id and call_id in self.calls:
            return
        block = tool_call(call_id, name, arguments)
        self.current(at).blocks.append(block)
        if call_id:
            self.calls[call_id] = block

    def result(self, call_id: str, output: str, is_error: bool) -> None:
        block = self.calls.get(call_id)
        if block is not None:
            block.update(output=clip(output), is_error=is_error)
