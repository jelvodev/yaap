"""Claude Code's session files: one JSON Lines file per session under ~/.claude/projects/<folder>/<session>.jsonl,
one line per event, with user and assistant messages in the Messages API format (tool_use blocks in the assistant's
message, their tool_result blocks in the next user line), linked by parentUuid."""

import json
import re
from dataclasses import dataclass
from typing import Any

from app.providers.session_format import ImportedSession, SessionTool, TurnBuilder, content_text, parse_time

COMMAND_NAME = re.compile(r"<command-name>\s*/?(.*?)\s*</command-name>", re.DOTALL)
COMMAND_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)


def claude_code_prompt(text: str) -> str:
    """What the user typed; a slash command as /name args, local command output left out."""
    if "<local-command-stdout>" in text or "<local-command-stderr>" in text:
        return ""
    name = COMMAND_NAME.search(text)
    if name:
        arguments = COMMAND_ARGS.search(text)
        return f"/{name.group(1)} {arguments.group(1).strip() if arguments else ''}".strip()
    return text


def active_branch(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The messages from the start to the last one, following parentUuid (a rewound session has side branches);
    compact boundaries link to the earlier part with logicalParentUuid. Without links, the file's order."""
    messages = [line for line in lines if line.get("type") in ("user", "assistant") and not line.get("isSidechain")]
    if not messages:
        return []
    by_id = {line["uuid"]: line for line in lines if isinstance(line.get("uuid"), str)}
    chain: list[dict[str, Any]] = []
    seen: set[str] = set()
    current: dict[str, Any] | None = messages[-1]
    while current is not None and current.get("uuid") not in seen:
        chain.append(current)
        if isinstance(current.get("uuid"), str):
            seen.add(current["uuid"])
        parent = current.get("parentUuid") or current.get("logicalParentUuid")
        current = by_id.get(parent) if isinstance(parent, str) else None
    chain.reverse()
    on_branch = [line for line in chain if line.get("type") in ("user", "assistant") and not line.get("isSidechain")]
    return on_branch if len(on_branch) > 1 or len(messages) == 1 else messages


def parse_claude_code(lines: list[dict[str, Any]]) -> ImportedSession:
    builder = TurnBuilder()
    # Claude Code writes one line per content block of a message; a block seen before in the same message is skipped.
    seen_blocks: dict[str, set[str]] = {}
    for line in active_branch(lines):
        message = line.get("message")
        if not isinstance(message, dict):
            continue
        at = parse_time(line.get("timestamp"))
        content = message.get("content")
        if line["type"] == "user":
            if line.get("isMeta"):
                continue
            results = [
                block for block in content if isinstance(block, dict) and block.get("type") == "tool_result"
            ] if isinstance(content, list) else []
            if results:
                for block in results:
                    builder.result(
                        str(block.get("tool_use_id") or ""),
                        content_text(block.get("content")),
                        bool(block.get("is_error")),
                    )
                continue
            builder.user(claude_code_prompt(content_text(content)), at)
            continue
        key = str(message.get("id") or line.get("uuid") or "")
        seen = seen_blocks.setdefault(key, set())
        for block in content if isinstance(content, list) else [{"type": "text", "text": content_text(content)}]:
            if not isinstance(block, dict):
                continue
            fingerprint = json.dumps(block, sort_keys=True, default=str)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            kind = block.get("type")
            if kind == "text":
                builder.text("text", str(block.get("text") or ""), at)
            elif kind == "thinking":
                builder.text("reasoning", str(block.get("thinking") or ""), at)
            elif kind == "tool_use":
                builder.call(str(block.get("id") or ""), str(block.get("name") or ""), block.get("input") or {}, at)
    first = next((line for line in lines if line.get("sessionId")), {})
    summary = next(
        (line.get("summary") for line in reversed(lines) if line.get("type") == "summary" and line.get("summary")),
        None,
    )
    times = [parse_time(line.get("timestamp")) for line in lines]
    return ImportedSession(
        tool="claude_code",
        session_id=first.get("sessionId"),
        title=str(summary) if summary else None,
        started_at=next((time for time in times if time is not None), None),
        cwd=first.get("cwd"),
        turns=builder.turns,
    )


@dataclass(frozen=True)
class ClaudeCodeSessions:
    tool: SessionTool = "claude_code"
    label: str = "Claude Code"
    folder: str = ".claude/projects"
    file_pattern: re.Pattern[str] = re.compile(r"^\.claude/projects/[^/]+/[^/]+\.jsonl$")
    per_folder: bool = True

    def matches(self, line: dict[str, Any]) -> bool:
        message_line = line.get("type") in ("user", "assistant") and isinstance(line.get("message"), dict)
        return "sessionId" in line or message_line

    def parse(self, lines: list[dict[str, Any]]) -> ImportedSession:
        return parse_claude_code(lines)


CLAUDE_CODE_SESSIONS = ClaudeCodeSessions()
