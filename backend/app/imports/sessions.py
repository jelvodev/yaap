"""Reading coding tools' session files: recognising the format and turning it into turns, and giving an imported
conversation to a model.

The formats themselves (Claude Code, Codex) live in providers/. A session becomes turns: what the user typed, and
everything the agent did after it (text, reasoning summaries, tool calls with their results) as YAAP's own content
blocks. Nothing in the file is run: this module only reads JSON.
"""

import json
from typing import Any

from app.providers.registry import SESSION_FORMATS, session_format
from app.providers.session_format import ImportedSession, SessionFormat, SessionImportError

# Marks an imported assistant message in place of a provider's raw turns (service.py).
IMPORTED_BLOCK_TYPE = "imported"
# What of each imported tool call the model gets when the conversation continues.
MODEL_INPUT_CHARS = 2000
MODEL_OUTPUT_CHARS = 4000


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


def detect(lines: list[dict[str, Any]]) -> SessionFormat:
    for item in lines[:50]:
        for candidate in SESSION_FORMATS:
            if candidate.matches(item):
                return candidate
    names = " or ".join(item.label for item in SESSION_FORMATS)
    raise SessionImportError(f"This is not a {names} session file.")


def parse_session(data: bytes) -> ImportedSession:
    """The session in a Claude Code or Codex file; raises SessionImportError when it is neither or has no messages."""
    lines = read_lines(data)
    if not lines:
        raise SessionImportError("The file has no JSON lines; a session file is JSON Lines.")
    found = detect(lines)
    session = found.parse(lines)
    session.turns = [turn for turn in session.turns if turn.user_text or turn.blocks]
    if not session.turns:
        raise SessionImportError(f"The {found.label} session has no messages to import.")
    return session


# --- Continuing an imported conversation -----------------------------------------------------------------------------


def is_imported(provider_raw: list[dict[str, Any]] | None) -> bool:
    return provider_raw is not None and len(provider_raw) > 0 and provider_raw[0].get("type") == IMPORTED_BLOCK_TYPE


def shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit // 2] + "\n[…]\n" + text[-limit // 2 :]


def imported_model_content(content: list[dict[str, Any]], tool: str) -> list[dict[str, Any]]:
    """An imported assistant message as the model gets it: its text, and each tool call as a note of what was done
    and what came back (shortened). The tool calls were made by another tool and are not YAAP's to replay."""
    known = session_format(tool)
    name = known.label if known is not None else tool
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
