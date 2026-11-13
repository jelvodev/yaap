"""Codex's session files: ~/.codex/sessions/YYYY/MM/DD/rollout-<time>-<id>.jsonl, a session_meta line, then
response_item lines in the Responses API format (message, reasoning, function_call and its function_call_output,
custom and local shell calls); older rollouts have the items without the wrapper."""

import json
import re
from dataclasses import dataclass
from typing import Any

from app.providers.session_format import ImportedSession, SessionTool, TurnBuilder, content_text, parse_time

# Text Codex adds to the user's messages itself: <environment_context>…</environment_context> and the like.
INJECTED = re.compile(r"^\s*<([a-z_]+)>[\s\S]*</\1>\s*$")
INJECTED_PREFIXES = ("# AGENTS.md instructions", "<user_instructions>", "<environment_context>")


def codex_output(output: Any) -> tuple[str, bool]:
    """A tool output: older Codex wraps it as JSON with the exit code in its metadata."""
    if isinstance(output, dict):
        output = output.get("content", output)
    if isinstance(output, list):
        parts = [part for part in output if isinstance(part, dict)]
        texts = [
            {**part, "type": "text"} if part.get("type") in ("input_text", "output_text") else part for part in parts
        ]
        return content_text(texts), False
    text = str(output if output is not None else "")
    try:
        wrapped = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return text, False
    if isinstance(wrapped, dict) and "output" in wrapped:
        metadata = wrapped.get("metadata") if isinstance(wrapped.get("metadata"), dict) else {}
        exit_code = metadata.get("exit_code")
        return str(wrapped.get("output") or ""), isinstance(exit_code, int) and exit_code != 0
    return text, False


def codex_arguments(arguments: Any) -> Any:
    if isinstance(arguments, str):
        try:
            return json.loads(arguments)
        except json.JSONDecodeError:
            return arguments
    return arguments


def is_injected(text: str) -> bool:
    stripped = text.strip()
    return bool(INJECTED.match(stripped)) or stripped.startswith(INJECTED_PREFIXES)


def parse_codex(lines: list[dict[str, Any]]) -> ImportedSession:
    builder = TurnBuilder()
    meta: dict[str, Any] = {}
    for line in lines:
        kind = line.get("type")
        at = parse_time(line.get("timestamp"))
        if kind == "session_meta" and isinstance(line.get("payload"), dict):
            meta = meta or line["payload"]
            continue
        if kind == "response_item" and isinstance(line.get("payload"), dict):
            item = line["payload"]
        elif "payload" in line or line.get("record_type"):
            continue
        elif kind is None and "instructions" in line and "id" in line:
            meta = meta or line
            continue
        else:
            item = line
        item_kind = item.get("type")
        if item_kind == "message":
            texts = [str(part.get("text") or "") for part in item.get("content") or [] if isinstance(part, dict)]
            if item.get("role") == "user":
                typed = [text for text in texts if text.strip() and not is_injected(text)]
                builder.user("\n\n".join(typed), at)
            elif item.get("role") == "assistant":
                builder.text("text", "\n\n".join(texts), at)
        elif item_kind == "reasoning":
            summary = [str(part.get("text") or "") for part in item.get("summary") or [] if isinstance(part, dict)]
            builder.text("reasoning", "\n\n".join(summary), at)
        elif item_kind == "function_call":
            call_id = str(item.get("call_id") or item.get("id") or "")
            builder.call(call_id, str(item.get("name") or ""), codex_arguments(item.get("arguments")), at)
        elif item_kind == "custom_tool_call":
            call_id = str(item.get("call_id") or item.get("id") or "")
            builder.call(call_id, str(item.get("name") or ""), {"input": item.get("input")}, at)
        elif item_kind == "local_shell_call":
            action = item.get("action") if isinstance(item.get("action"), dict) else {}
            builder.call(str(item.get("call_id") or item.get("id") or ""), "local_shell", action, at)
        elif item_kind == "web_search_call":
            action = item.get("action") if isinstance(item.get("action"), dict) else {}
            builder.call(str(item.get("id") or ""), "web_search", action, at)
        elif item_kind in ("function_call_output", "custom_tool_call_output", "local_shell_call_output"):
            output, is_error = codex_output(item.get("output"))
            builder.result(str(item.get("call_id") or ""), output, is_error)
    return ImportedSession(
        tool="codex",
        session_id=meta.get("id"),
        title=None,
        started_at=parse_time(meta.get("timestamp"))
        or next((time for line in lines if (time := parse_time(line.get("timestamp")))), None),
        cwd=meta.get("cwd"),
        turns=builder.turns,
    )


@dataclass(frozen=True)
class CodexSessions:
    tool: SessionTool = "codex"
    label: str = "Codex"
    folder: str = ".codex/sessions"
    file_pattern: re.Pattern[str] = re.compile(r"^\.codex/sessions/(?:[^/]+/)*rollout-[^/]+\.jsonl$")
    per_folder: bool = False

    def matches(self, line: dict[str, Any]) -> bool:
        kind = line.get("type")
        return (
            (kind in ("session_meta", "response_item", "turn_context", "event_msg") and "payload" in line)
            or (kind == "message" and line.get("role") in ("user", "assistant", "developer"))
            or ("instructions" in line and "id" in line)
        )

    def parse(self, lines: list[dict[str, Any]]) -> ImportedSession:
        return parse_codex(lines)


CODEX_SESSIONS = CodexSessions()
