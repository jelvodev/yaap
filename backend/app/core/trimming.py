"""Shortening old tool output.

Before a conversation is summarised, a cheaper step: once a reply filled `trim_at` percent of the context limit,
the long tool results of every turn before the last KEEP_TURNS are shortened to their start and end in
what the model gets back. Nothing stored changes except a `trim` block on the last message the trim covers, so the prefix the model gets
stays the same until the next trim and the prompt cache holds. Not for Claude Code and Codex, like summaries.
"""

from typing import Any

from app.core.context_control import resolve_context_control
from app.core.summaries import KEEP_TURNS, latest_summary
from app.providers.anthropic_provider import TURNS_BLOCK_TYPE
from app.providers.base import HistoryMessage
from app.providers.openai_provider import ITEMS_BLOCK_TYPE
from app.providers.registry import ProviderRegistry
from app.storage.models import Conversation, Message

LONG_OUTPUT = 2000
KEEP_CHARS = 600


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


def shrink_raw(raw: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """The stored reply with its long tool results shortened, for both provider formats."""
    if not raw:
        return raw
    shrunk = []
    for block in raw:
        if block.get("type") == TURNS_BLOCK_TYPE:
            messages = []
            for turn in block["messages"]:
                if turn.get("role") == "user" and isinstance(turn.get("content"), list):
                    turn = {
                        **turn,
                        "content": [
                            {**part, "content": shorten_result_content(part.get("content"))}
                            if part.get("type") == "tool_result"
                            else part
                            for part in turn["content"]
                        ],
                    }
                messages.append(turn)
            block = {**block, "messages": messages}
        elif block.get("type") == "chat_messages":
            block = {
                **block,
                "messages": [
                    {**item, "content": shorten(item["content"])}
                    if item.get("role") == "tool" and isinstance(item.get("content"), str)
                    else item
                    for item in block["messages"]
                ],
            }
        elif block.get("type") == ITEMS_BLOCK_TYPE:
            block = {
                **block,
                "items": [
                    {**item, "output": shorten(item["output"])}
                    if item.get("type") == "function_call_output" and isinstance(item.get("output"), str)
                    else item
                    for item in block["items"]
                ],
            }
        shrunk.append(block)
    return shrunk


def apply_trim(branch: list[Message], history: list[HistoryMessage]) -> list[HistoryMessage]:
    """The history with the tool results of every message up to the latest trim point shortened."""
    from app.core.chat import is_part_of_history

    index = latest_trim(branch)
    if index is None:
        return history
    in_history = [message for message in branch if is_part_of_history(message)]
    if len(in_history) != len(history):
        return history
    trimmed_ids = {id(message) for message in branch[: index + 1]}
    return [
        HistoryMessage(role=item.role, content=item.content, provider_raw=shrink_raw(item.provider_raw))
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
    from app.core.chat import is_part_of_history

    turn_ends = [index for index, message in enumerate(branch) if message.role == "assistant" and is_part_of_history(message)]
    if len(turn_ends) < KEEP_TURNS + 1:
        return None
    point = turn_ends[-(KEEP_TURNS + 1)]
    start = max(latest_trim(branch) or -1, latest_summary(branch) or -1) + 1
    if point < start or not has_long_output(branch[start : point + 1]):
        return None
    return point


def mark_trim(session: Any, branch: list[Message]) -> bool:
    point = trim_point(branch)
    if point is None:
        return False
    message = branch[point]
    message.content = [*(block for block in message.content if block.get("type") != "trim"), {"type": "trim"}]
    session.commit()
    return True


def needs_trim(session: Any, providers: ProviderRegistry, conversation: Conversation, used: int) -> bool:
    """Whether the last reply filled trim_at percent of the context limit, with auto_compact on."""
    return resolve_context_control(session, providers, conversation).needs_trim(used)
