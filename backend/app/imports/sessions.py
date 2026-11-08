"""Reading Claude Code and Codex session files.

Claude Code keeps one JSON Lines file per session under ~/.claude/projects/<folder>/<session>.jsonl: one line per
event, with user and assistant messages in the Messages API format (tool_use blocks in the assistant's message, their
tool_result blocks in the next user line), linked by parentUuid. Codex keeps ~/.codex/sessions/YYYY/MM/DD/
rollout-<time>-<id>.jsonl: a session_meta line, then response_item lines in the Responses API format (message,
reasoning, function_call and its function_call_output, custom and local shell calls); older rollouts have the items
without the wrapper.

Both become turns: what the user typed, and everything the agent did after it (text, reasoning summaries, tool calls
with their results) as YAAP's own content blocks. Nothing in the file is run: this module only reads JSON.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

CLAUDE_CODE = "claude_code"
CODEX = "codex"
TOOL_NAMES = {CLAUDE_CODE: "Claude Code", CODEX: "Codex"}
# Marks an imported assistant message in place of a provider's raw turns (service.py).
IMPORTED_BLOCK_TYPE = "imported"
# What of each imported tool call the model gets when the conversation continues.
MODEL_INPUT_CHARS = 2000
MODEL_OUTPUT_CHARS = 4000
# As stored for YAAP's own tool calls (core/chat.py).
MAX_TOOL_OUTPUT = 20_000
MAX_TEXT = 200_000
# Text Codex adds to the user's messages itself: <environment_context>…</environment_context> and the like.
INJECTED = re.compile(r"^\s*<([a-z_]+)>[\s\S]*</\1>\s*$")
INJECTED_PREFIXES = ("# AGENTS.md instructions", "<user_instructions>", "<environment_context>")
COMMAND_NAME = re.compile(r"<command-name>\s*/?(.*?)\s*</command-name>", re.DOTALL)
COMMAND_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)


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
    tool: str
    session_id: str | None
    title: str | None
    started_at: datetime | None
    cwd: str | None
    turns: list[Turn]

    @property
    def tool_calls(self) -> int:
        return sum(1 for turn in self.turns for block in turn.blocks if block["type"] == "tool_call")


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def read_lines(data: bytes) -> list[dict[str, Any]]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise SessionImportError("The file is not UTF-8 text; a session file is JSON Lines.") from error
    lines = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            lines.append(item)
    return lines


def detect(lines: list[dict[str, Any]]) -> str:
    for item in lines[:50]:
        kind = item.get("type")
        if kind in ("session_meta", "response_item", "turn_context", "event_msg") and "payload" in item:
            return CODEX
        if "sessionId" in item or (kind in ("user", "assistant") and isinstance(item.get("message"), dict)):
            return CLAUDE_CODE
        if kind == "message" and item.get("role") in ("user", "assistant", "developer"):
            return CODEX
        if "instructions" in item and "id" in item:
            return CODEX
    raise SessionImportError("This is not a Claude Code or Codex session file.")


def clip(text: str, limit: int = MAX_TOOL_OUTPUT) -> str:
    return text if len(text) <= limit else text[:limit] + "\n[Output shortened for storage.]"


def tool_call(call_id: str, name: str, arguments: Any) -> dict[str, Any]:
    tool_input = arguments if isinstance(arguments, dict) else {"input": arguments}
    # The result is filled in when it comes; a call without one keeps an empty output (never "running").
    return {"type": "tool_call", "id": call_id, "name": name or "tool", "input": tool_input, "output": "", "is_error": False, "imported": True}


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


# --- Claude Code -------------------------------------------------------------------------------------------------------


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
            if isinstance(content, list) and any(isinstance(block, dict) and block.get("type") == "tool_result" for block in content):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        builder.result(str(block.get("tool_use_id") or ""), content_text(block.get("content")), bool(block.get("is_error")))
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
    summary = next((line.get("summary") for line in reversed(lines) if line.get("type") == "summary" and line.get("summary")), None)
    times = [parse_time(line.get("timestamp")) for line in lines]
    return ImportedSession(
        tool=CLAUDE_CODE,
        session_id=first.get("sessionId"),
        title=str(summary) if summary else None,
        started_at=next((time for time in times if time is not None), None),
        cwd=first.get("cwd"),
        turns=builder.turns,
    )


# --- Codex -------------------------------------------------------------------------------------------------------------


def codex_output(output: Any) -> tuple[str, bool]:
    """A tool output: older Codex wraps it as JSON with the exit code in its metadata."""
    if isinstance(output, dict):
        output = output.get("content", output)
    if isinstance(output, list):
        return content_text([{**part, "type": "text"} if part.get("type") in ("input_text", "output_text") else part for part in output if isinstance(part, dict)]), False
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
            builder.call(str(item.get("call_id") or item.get("id") or ""), str(item.get("name") or ""), codex_arguments(item.get("arguments")), at)
        elif item_kind == "custom_tool_call":
            builder.call(str(item.get("call_id") or item.get("id") or ""), str(item.get("name") or ""), {"input": item.get("input")}, at)
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
        tool=CODEX,
        session_id=meta.get("id"),
        title=None,
        started_at=parse_time(meta.get("timestamp")) or next((time for line in lines if (time := parse_time(line.get("timestamp")))), None),
        cwd=meta.get("cwd"),
        turns=builder.turns,
    )


def parse_session(data: bytes) -> ImportedSession:
    """The session in a Claude Code or Codex file; raises SessionImportError when it is neither or has no messages."""
    lines = read_lines(data)
    if not lines:
        raise SessionImportError("The file has no JSON lines; a session file is JSON Lines.")
    tool = detect(lines)
    session = parse_claude_code(lines) if tool == CLAUDE_CODE else parse_codex(lines)
    session.turns = [turn for turn in session.turns if turn.user_text or turn.blocks]
    if not session.turns:
        raise SessionImportError(f"The {TOOL_NAMES[tool]} session has no messages to import.")
    return session


# --- Continuing an imported conversation -----------------------------------------------------------------------------


def is_imported(provider_raw: list[dict[str, Any]] | None) -> bool:
    return bool(provider_raw) and provider_raw[0].get("type") == IMPORTED_BLOCK_TYPE  # type: ignore[index]


def shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit // 2] + "\n[…]\n" + text[-limit // 2 :]


def imported_model_content(content: list[dict[str, Any]], tool: str) -> list[dict[str, Any]]:
    """An imported assistant message as the model gets it: its text, and each tool call as a note of what was done
    and what came back (shortened). The tool calls were made by another tool and are not YAAP's to replay."""
    name = TOOL_NAMES.get(tool, tool)
    parts: list[str] = []
    for block in content:
        kind = block.get("type")
        if kind == "text" and block.get("text"):
            parts.append(block["text"])
        elif kind == "tool_call":
            arguments = shorten(json.dumps(block.get("input") or {}, ensure_ascii=False), MODEL_INPUT_CHARS)
            output = shorten(str(block.get("output") or "(no output)"), MODEL_OUTPUT_CHARS)
            state = "failed" if block.get("is_error") else "returned"
            parts.append(f"[{name} called its tool {block.get('name')} with {arguments}; it {state}:\n{output}]")
    return [{"type": "text", "text": "\n\n".join(parts) or "(empty)"}]
