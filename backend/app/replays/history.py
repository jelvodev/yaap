"""The messages a replay starts with: what the replayed call saw, rebuilt from its snapshot.

A context snapshot (tracing/snapshots.py) keeps the call's message list in YAAP's neutral form: the request's history
(turn context included in the newest user message) followed by what the reply had done before that call, its text and
its tool calls with their results. Secret values are already `[secret:NAME]` there and file data is left out.

`snapshot_messages` reads that list back as it was stored. A provider replays an earlier assistant turn's tool calls
only from its own raw form, which a snapshot does not keep (and which another vendor could not read anyway), so
`model_history` writes each tool call as a note in its assistant turn and its result as a user turn right after it,
the way the native forms alternate. The model then sees the same messages, as text, whatever provider replays them.
"""

import json
from collections.abc import Sequence
from typing import Literal

from app.core.turn_context import HEADING as TURN_CONTEXT_HEADING
from app.providers.base import ContentBlock, HistoryMessage
from app.tracing.snapshots import LEFT_OUT, JsonValue, StoredSnapshot

FILE_BLOCKS = frozenset({"image", "document"})
# Kept as the model saw them; other neutral blocks (reasoning) are not model input on a later call.
KEPT_BLOCKS = frozenset({"text", "image", "document"})
LEFT_OUT_NOTE = "[A file was attached here; its data is not kept in the snapshot.]"


class SnapshotMessagesError(Exception):
    """The snapshot's message list cannot be read back; the message is safe to show."""


def snapshot_messages(stored: StoredSnapshot) -> list[HistoryMessage]:
    """The message list a model call saw, as stored."""
    if not stored.complete:
        raise SnapshotMessagesError("Some of what this call saw was pruned, so it cannot be rebuilt.")
    messages = [message_from_json(value) for value in stored.messages]
    if not messages:
        raise SnapshotMessagesError("This call saw no messages.")
    return messages


def message_from_json(value: JsonValue) -> HistoryMessage:
    if not isinstance(value, dict):
        raise SnapshotMessagesError("A stored message has an unknown form.")
    role, content = value.get("role"), value.get("content")
    if role not in ("user", "assistant") or not isinstance(content, list):
        raise SnapshotMessagesError("A stored message has an unknown form.")
    blocks: list[ContentBlock] = [dict(block) for block in content if isinstance(block, dict)]
    checked_role: Literal["user", "assistant"] = "user" if role == "user" else "assistant"
    return HistoryMessage(role=checked_role, content=blocks)


def model_history(messages: Sequence[HistoryMessage]) -> list[HistoryMessage]:
    """The messages as every provider reads them: tool calls and their results as text, alternating as the native
    forms do; file blocks without their data as a note."""
    history: list[HistoryMessage] = []
    for message in messages:
        if message.role == "user":
            history.append(HistoryMessage(role="user", content=user_blocks(message.content)))
            continue
        said: list[ContentBlock] = []
        results: list[ContentBlock] = []
        for block in message.content:
            kind = block.get("type")
            if kind == "tool_call":
                said.append({"type": "text", "text": call_note(block)})
                results.append({"type": "text", "text": result_note(block)})
            elif kind == "text" and block.get("text"):
                if results:
                    # Text after tool calls came from the next model call: close this turn first.
                    history += [HistoryMessage("assistant", said), HistoryMessage("user", results)]
                    said, results = [], []
                said.append({"type": "text", "text": str(block["text"])})
        if said:
            history.append(HistoryMessage(role="assistant", content=said))
        if results:
            history.append(HistoryMessage(role="user", content=results))
    return history


def user_blocks(content: Sequence[ContentBlock]) -> list[ContentBlock]:
    blocks: list[ContentBlock] = []
    for block in content:
        kind = block.get("type")
        if kind in FILE_BLOCKS and block.get("data") == LEFT_OUT:
            blocks.append({"type": "text", "text": LEFT_OUT_NOTE})
        elif kind in KEPT_BLOCKS:
            blocks.append(dict(block))
    return blocks


def call_note(block: ContentBlock) -> str:
    arguments = json.dumps(block.get("input") or {}, ensure_ascii=False, sort_keys=True)
    return f"[Called the tool {block.get('name')} (call {block.get('id')}) with {arguments}]"


def result_note(block: ContentBlock) -> str:
    state = "failed" if block.get("is_error") else "returned"
    output = block.get("output")
    text = str(output) if output is not None else "(no output: the call did not finish)"
    return f"[The tool {block.get('name')} (call {block.get('id')}) {state}:\n{text}]"


def last_user_text(messages: Sequence[HistoryMessage]) -> str:
    """The text of the newest user message that has text of its own, without the turn context block."""
    for message in reversed(messages):
        if message.role != "user":
            continue
        texts = [
            str(block.get("text") or "").strip()
            for block in message.content
            if block.get("type") == "text" and not str(block.get("text") or "").startswith(TURN_CONTEXT_HEADING)
        ]
        text = "\n\n".join(part for part in texts if part)
        if text:
            return text
    return ""

