"""Shortening old tool output.

Before a conversation is summarised, a cheaper step: once a reply filled `trim_at` percent of the context limit,
the long tool results of every turn before the last KEEP_TURNS are shortened to their start and end in
what the model gets back. Nothing stored changes except a `trim` block on the last message the trim covers, so the prefix the model gets
stays the same until the next trim and the prompt cache holds. Not for Claude Code and Codex, like summaries.
"""

from typing import Any

from app.agent.file_tools import DEFAULT_READ_LINES
from app.core.chat_history import is_part_of_history
from app.core.context_control import resolve_context_control
from app.core.context_savings import context_savings_on
from app.core.summaries import KEEP_TURNS, latest_summary
from app.providers.anthropic_provider import TURNS_BLOCK_TYPE
from app.providers.base import HistoryMessage
from app.providers.openai_provider import ITEMS_BLOCK_TYPE
from app.providers.registry import ProviderRegistry
from app.storage.models import Conversation, Message

LONG_OUTPUT = 2000
KEEP_CHARS = 600
WRITE_TOOLS = frozenset({"write_file", "edit_file"})


def trim_block(message: Message) -> dict[str, Any] | None:
    return next((block for block in message.content if block.get("type") == "trim"), None)


def latest_trim(branch: list[Message]) -> int | None:
    for index in range(len(branch) - 1, -1, -1):
        if trim_block(branch[index]) is not None:
            return index
    return None


def shorten(text: str) -> str:
    if len(text) <= LONG_OUTPUT:
        return text
    left_out = len(text) - 2 * KEEP_CHARS
    return f"{text[:KEEP_CHARS]}\n[… {left_out} characters of old output left out to save context …]\n{text[-KEEP_CHARS:]}"


def shorten_result_content(content: Any) -> Any:
    if isinstance(content, str):
        return shorten(content)
    if isinstance(content, list):
        return [{**part, "text": shorten(part["text"])} if part.get("type") == "text" else part for part in content]
    return content


def stub_content(original: Any, stub: str) -> Any:
    """The stub in the shape the result had: a string stays a string, a list of parts becomes one text part."""
    return [{"type": "text", "text": stub}] if isinstance(original, list) else stub


def shrink_anthropic_turns(block: dict[str, Any], stubs: dict[str, str]) -> dict[str, Any]:
    def result(part: dict[str, Any]) -> dict[str, Any]:
        stub = stubs.get(part.get("tool_use_id"))
        content = stub_content(part.get("content"), stub) if stub else shorten_result_content(part.get("content"))
        return {**part, "content": content}

    messages = []
    for turn in block["messages"]:
        if turn.get("role") == "user" and isinstance(turn.get("content"), list):
            parts = [result(part) if part.get("type") == "tool_result" else part for part in turn["content"]]
            turn = {**turn, "content": parts}
        messages.append(turn)
    return {**block, "messages": messages}


def shrink_chat_messages(block: dict[str, Any], stubs: dict[str, str]) -> dict[str, Any]:
    return {
        **block,
        "messages": [
            {**item, "content": stubs.get(item.get("tool_call_id")) or shorten(item["content"])}
            if item.get("role") == "tool" and isinstance(item.get("content"), str)
            else item
            for item in block["messages"]
        ],
    }


def shrink_openai_items(block: dict[str, Any], stubs: dict[str, str]) -> dict[str, Any]:
    return {
        **block,
        "items": [
            {**item, "output": stubs.get(item.get("call_id")) or shorten(item["output"])}
            if item.get("type") == "function_call_output" and isinstance(item.get("output"), str)
            else item
            for item in block["items"]
        ],
    }


def shrink_raw(raw: list[dict[str, Any]] | None, stubs: dict[str, str] | None = None) -> list[dict[str, Any]] | None:
    """The stored reply with its long tool results shortened, for all provider formats. A result whose tool-use id
    is in `stubs` becomes that text instead."""
    if not raw:
        return raw
    stubs = stubs or {}
    shrunk = []
    for block in raw:
        if block.get("type") == TURNS_BLOCK_TYPE:
            block = shrink_anthropic_turns(block, stubs)
        elif block.get("type") == "chat_messages":
            block = shrink_chat_messages(block, stubs)
        elif block.get("type") == ITEMS_BLOCK_TYPE:
            block = shrink_openai_items(block, stubs)
        shrunk.append(block)
    return shrunk


def apply_trim(branch: list[Message], history: list[HistoryMessage]) -> list[HistoryMessage]:
    """The history with the tool results of every message up to the latest trim point shortened."""

    index = latest_trim(branch)
    if index is None:
        return history
    in_history = [message for message in branch if is_part_of_history(message)]
    if len(in_history) != len(history):
        return history
    trimmed_ids = {id(message) for message in branch[: index + 1]}
    stubs = stale_stubs(branch, set(trim_block(branch[index]).get("stale") or []))
    return [
        HistoryMessage(role=item.role, content=item.content, provider_raw=shrink_raw(item.provider_raw, stubs))
        if id(message) in trimmed_ids and item.role == "assistant"
        else item
        for message, item in zip(in_history, history, strict=True)
    ]


def has_long_output(messages: list[Message]) -> bool:
    return any(
        block.get("type") == "tool_call" and len(str(block.get("output") or "")) > LONG_OUTPUT
        for message in messages
        for block in message.content
    )


def trim_point(branch: list[Message]) -> int | None:
    """The end of the turn before the last KEEP_TURNS, when it is later than the last trim and summary and there is
    long output to shorten in between."""

    turn_ends = [index for index, message in enumerate(branch) if message.role == "assistant" and is_part_of_history(message)]
    if len(turn_ends) < KEEP_TURNS + 1:
        return None
    point = turn_ends[-(KEEP_TURNS + 1)]
    start = max(latest_trim(branch) or -1, latest_summary(branch) or -1) + 1
    if point < start or not has_long_output(branch[start : point + 1]):
        return None
    return point


def normal_path(path: Any) -> str:
    text = str(path or "").replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


def tool_calls(branch: list[Message]) -> list[dict[str, Any]]:
    return [block for message in branch for block in message.content if block.get("type") == "tool_call"]


def stale_reads(branch: list[Message]) -> set[str]:
    """The ids of `read_file` calls whose file a later successful `write_file` or `edit_file` changed."""
    stale: set[str] = set()
    reads: dict[str, list[str]] = {}
    for call in tool_calls(branch):
        path = normal_path((call.get("input") or {}).get("path"))
        if call.get("name") == "read_file" and call.get("id"):
            reads.setdefault(path, []).append(call["id"])
        elif call.get("name") in WRITE_TOOLS and not call.get("is_error"):
            stale.update(reads.pop(path, []))
    return stale


def stale_stub(call: dict[str, Any]) -> str:
    arguments = call.get("input") or {}
    path = arguments.get("path")
    if arguments.get("outline"):
        return f"Outline of {path}, left out: the file changed since; read it again."
    first = int(arguments.get("offset") or 1)
    last = first + int(arguments.get("limit") or DEFAULT_READ_LINES) - 1
    return f"Read of {path} lines {first}–{last}, left out: the file changed since; read it again."


def stale_stubs(branch: list[Message], stale: set[str]) -> dict[str, str]:
    """The stub text per stale tool-use id, from the arguments stored in the branch."""
    if not stale:
        return {}
    return {call["id"]: stale_stub(call) for call in tool_calls(branch) if call.get("id") in stale}


def context_savings_for(session: Any, branch: list[Message]) -> bool:
    conversation_id = branch[0].conversation_id if branch else None
    conversation = session.get(Conversation, conversation_id) if conversation_id is not None else None
    return conversation is not None and context_savings_on(session, conversation.environment_id)


def mark_trim(session: Any, branch: list[Message]) -> bool:
    point = trim_point(branch)
    if point is None:
        return False
    message = branch[point]
    block: dict[str, Any] = {"type": "trim"}
    if context_savings_for(session, branch):
        reads_up_to_point = {call["id"] for call in tool_calls(branch[: point + 1]) if call.get("name") == "read_file"}
        block["stale"] = sorted(stale_reads(branch) & reads_up_to_point)
    message.content = [*(item for item in message.content if item.get("type") != "trim"), block]
    session.commit()
    return True


def needs_trim(session: Any, providers: ProviderRegistry, conversation: Conversation, used: int) -> bool:
    """Whether the last reply filled trim_at percent of the context limit, with auto_compact on."""
    return resolve_context_control(session, providers, conversation).needs_trim(used)
