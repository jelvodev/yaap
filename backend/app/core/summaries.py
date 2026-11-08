"""Summarising long conversations.

When a conversation passes `compact_at` percent of its context limit (`app/core/context_control.py`), a
cheaper model summarises everything before the last KEEP_TURNS turns. The summary is a `summary` block on the last message it covers;
from then on the model receives the summary and the messages after that message. The messages themselves stay for
reading, search and export. Claude Code and Codex compact their own sessions, so they are left alone.
"""

import json
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.background import BackgroundWork
from app.core.utility import UtilityContext, UtilitySkipped, run_utility_or_skip
from app.providers.base import HistoryMessage
from app.providers.registry import ProviderRegistry
from app.storage.models import Conversation, Message, UsageRecord

logger = logging.getLogger(__name__)

KEEP_TURNS = 4
MAX_TRANSCRIPT_CHARS = 200_000
MAX_TOOL_CHARS = 600
SUMMARY_TOKENS = 4000
SUMMARY_HEADING = "[Summary of the earlier part of this conversation]"
# Skills and standards the agent loaded before the summary come along after it, newest first, within this budget.
CARRIED_CHARS = 48_000
LOADING_TOOLS = ("load_skill", "load_standard")
# The built-in command /compact [focus], unless the Blueprint has a command named compact.
COMPACT_COMMAND = re.compile(r"^/compact(?:\s+(\S.*))?$", re.DOTALL)
MAX_FOCUS_CHARS = 500
TOO_LITTLE = f"There is not enough to summarise yet: the last {KEEP_TURNS} turns always stay whole."


class SummaryError(Exception):
    """The conversation cannot be summarised now; the message says why."""


def summary_block(message: Message) -> dict[str, Any] | None:
    return next((block for block in message.content if block.get("type") == "summary"), None)


def latest_summary(branch: list[Message]) -> int | None:
    """Index in the branch of the last message that carries a summary."""
    for index in range(len(branch) - 1, -1, -1):
        if summary_block(branch[index]) is not None:
            return index
    return None


def apply_summary(branch: list[Message], history: list[HistoryMessage]) -> list[HistoryMessage]:
    """The model's history: from a summary on, the summary and the messages after the message that carries it.

    `history` is built from `branch` by build_history, which leaves out some messages; they are matched by position
    of the messages after the summary. The summary goes in front of the first user message that follows.
    """
    index = latest_summary(branch)
    if index is None:
        return history
    after = {id(message) for message in branch[index + 1 :]}
    kept_count = sum(1 for message in branch if id(message) in after and _in_history(message))
    kept = history[len(history) - kept_count :] if kept_count else []
    text = f"{SUMMARY_HEADING}\n\n{summary_block(branch[index])['text']}"  # type: ignore[index]
    blocks = [{"type": "text", "text": text}, *({"type": "text", "text": part} for part in carried_texts(branch[: index + 1]))]
    if kept and kept[0].role == "user":
        first = kept[0]
        kept[0] = HistoryMessage(role="user", content=[*blocks, *first.content], provider_raw=first.provider_raw)
        return kept
    return [HistoryMessage(role="user", content=blocks), *kept]


@dataclass
class Carried:
    """What the summary leaves out but the model still needs: pinned skills and loaded skills and standards."""

    pinned: dict[str, str]
    loaded: list[tuple[str, str, str]]  # (tool, name, text), newest first, within the budget
    dropped: list[str]


def carried(covered: list[Message]) -> Carried:
    pinned: dict[str, str] = {}
    loaded: dict[tuple[str, str], str] = {}
    for message in covered:
        for block in message.content:
            kind = block.get("type")
            if message.role == "user" and kind == "skill":
                pinned[block["name"]] = block.get("text", "")
            elif message.role == "user" and kind == "skill_removed":
                pinned.pop(block["name"], None)
            elif kind == "tool_call" and block.get("name") in LOADING_TOOLS and not block.get("is_error") and block.get("output"):
                name = str((block.get("input") or {}).get("name") or "")
                key = (block["name"], name)
                loaded.pop(key, None)  # The newest load counts.
                loaded[key] = str(block["output"])
    chosen: list[tuple[str, str, str]] = []
    dropped: list[str] = []
    budget = CARRIED_CHARS
    for (tool, name), text in reversed(list(loaded.items())):
        if tool == "load_skill" and name in pinned:
            continue
        if len(text) <= budget:
            chosen.append((tool, name, text))
            budget -= len(text)
        else:
            dropped.append(name)
    return Carried(pinned, chosen, dropped)


def carried_texts(covered: list[Message]) -> list[str]:
    kept = carried(covered)
    texts = [f'<skill name="{name}">\n{text}\n</skill>' for name, text in kept.pinned.items()]
    texts += [
        f'<{"skill" if tool == "load_skill" else "standard"} name="{name}" loaded="earlier">\n{text}\n</{"skill" if tool == "load_skill" else "standard"}>'
        for tool, name, text in kept.loaded
    ]
    if kept.dropped:
        texts.append(f"[Loaded earlier but not repeated here, load again when needed: {', '.join(kept.dropped)}.]")
    return texts


def _in_history(message: Message) -> bool:
    from app.core.chat import is_part_of_history

    return is_part_of_history(message)


def cover_point(branch: list[Message]) -> int | None:
    """Index of the assistant message a new summary would cover up to: the end of the turn before the last
    KEEP_TURNS, and later than the current summary. None when there is too little to summarise."""
    from app.core.chat import is_part_of_history

    turn_ends = [index for index, message in enumerate(branch) if message.role == "assistant" and is_part_of_history(message)]
    if len(turn_ends) < KEEP_TURNS + 1:
        return None
    point = turn_ends[-(KEEP_TURNS + 1)]
    previous = latest_summary(branch)
    if previous is not None and point <= previous:
        return None
    return point


def block_text(block: dict[str, Any]) -> str:
    kind = block.get("type")
    if kind in ("text", "command"):
        return str(block.get("text") or "")
    if kind == "tool_call":
        arguments = json.dumps(block.get("input") or {}, ensure_ascii=False)[:MAX_TOOL_CHARS]
        output = str(block.get("output") or "")[:MAX_TOOL_CHARS]
        return f"[tool {block.get('name')} {arguments}{' failed' if block.get('is_error') else ''}: {output}]"
    if kind == "attachment":
        return f"[attachment {block.get('name')}]"
    return ""


def transcript(messages: list[Message]) -> str:
    parts = []
    for message in messages:
        text = "\n".join(part for block in message.content if (part := block_text(block).strip()))
        if text:
            parts.append(f"{'User' if message.role == 'user' else 'Assistant'}:\n{text}")
    joined = "\n\n".join(parts)
    # The newest part matters most for what follows; a very long start is shortened.
    return joined if len(joined) <= MAX_TRANSCRIPT_CHARS else "[…]\n" + joined[-MAX_TRANSCRIPT_CHARS:]


def compact_focus(text: str) -> str | None:
    """The focus of a `/compact [focus]` message: "" without one, None when the message is not /compact."""
    match = COMPACT_COMMAND.match(text.strip())
    if match is None:
        return None
    return " ".join((match.group(1) or "").split())[:MAX_FOCUS_CHARS]


def summary_prompt(previous: str | None, messages: list[Message], focus: str | None = None) -> str:
    earlier = f"Summary so far:\n{previous}\n\nWhat followed:\n\n" if previous else ""
    focused = (
        f"The user asks the summary to focus on: {focus}. Keep everything about it in full detail, even when it "
        "seems minor; other topics may be shorter.\n\n"
        if focus
        else ""
    )
    return (
        "Summarise this conversation between a user and an AI development assistant, so the assistant can continue "
        "the work without the full text. Keep: the goal and the user's requests, decisions and their reasons, "
        "constraints and preferences the user stated, files and commands that matter, what was done and what is "
        "still open, and errors with their causes. Skills and standards stay available apart from the summary, so do "
        "not summarise their text. Leave out pleasantries and repeated details. Write in the "
        "language of the conversation, as compact notes under short headings.\n\n"
        f"{focused}{earlier}{transcript(messages)}"
    )


@dataclass
class SummaryResult:
    covered_message_id: int
    covered: int


async def summarise(context: UtilityContext, conversation_id: int, focus: str | None = None) -> SummaryResult:
    """Summarise the conversation up to the turn before the last KEEP_TURNS; the summary is stored on the last
    message it covers. focus (from /compact) is an extra instruction for what the summary keeps."""
    from app.core.chat import load_branch

    with context.session_factory() as session:
        conversation = session.get_one(Conversation, conversation_id)
        branch = load_branch(session, conversation_id, conversation.head_message_id)
        point = cover_point(branch)
        if point is None:
            raise SummaryError(TOO_LITTLE)
        previous_index = latest_summary(branch)
        previous = summary_block(branch[previous_index])["text"] if previous_index is not None else None  # type: ignore[index]
        start = previous_index + 1 if previous_index is not None else 0
        messages = branch[start : point + 1]
        covered_id = branch[point].id
        environment_id = conversation.environment_id
        prompt = summary_prompt(previous, messages, focus)
    text = await run_utility_or_skip(
        context, environment_id, prompt, conversation_id, max_tokens=SUMMARY_TOKENS, task="summary", role="worker"
    )
    if isinstance(text, UtilitySkipped):
        raise SummaryError(text.message)
    if not text:
        raise SummaryError("No model was available to write the summary. Check the connection and the budget.")
    with context.session_factory() as session:
        message = session.get_one(Message, covered_id)
        content = [block for block in message.content if block.get("type") != "summary"]
        content.append(
            {"type": "summary", "text": text.strip(), "covered": point + 1, **({"focus": focus} if focus else {})}
        )
        message.content = content
        session.commit()
    return SummaryResult(covered_id, point + 1)


def yaap_compacts(session: Session, providers: ProviderRegistry, conversation: Conversation) -> bool:
    """Whether YAAP summarises this conversation: every connection but Claude Code and Codex."""
    from app.core.context_control import compacts_itself

    return not compacts_itself(session, providers, conversation)


def needs_summary(session: Session, providers: ProviderRegistry, conversation: Conversation) -> bool:
    """Whether the last reply filled compact_at percent of the context limit, with auto_compact on."""
    from app.core.context_control import resolve_context_control

    return resolve_context_control(session, providers, conversation).needs_summary(context_used(session, conversation))


def context_used(session: Session, conversation: Conversation) -> int | None:
    """The context the last reply used, in tokens."""
    last = session.scalar(
        select(UsageRecord)
        .where(UsageRecord.conversation_id == conversation.id, UsageRecord.purpose == "main")
        .order_by(UsageRecord.id.desc())
    )
    if last is None:
        return None
    return last.context_tokens if last.context_tokens is not None else (
        last.input_tokens + last.cache_read_tokens + last.cache_write_tokens + last.output_tokens
    )


async def summarise_if_needed(context: UtilityContext, conversation_id: int) -> bool:
    """After a reply: summarise when the context is filling up. Never raises; a summary is a nicety."""
    try:
        with context.session_factory() as session:
            conversation = session.get_one(Conversation, conversation_id)
            from app.core.chat import load_branch

            from app.core.context_control import resolve_context_control
            from app.core.trimming import mark_trim

            branch = load_branch(session, conversation_id, conversation.head_message_id)
            control = resolve_context_control(session, context.providers, conversation)
            used = context_used(session, conversation)
            if not control.needs_summary(used) or cover_point(branch) is None:
                # The cheaper step first: shorten old tool output.
                if control.needs_trim(used):
                    mark_trim(session, branch)
                return False
        await summarise(context, conversation_id)
        return True
    except SummaryError as error:
        logger.info("No summary for conversation %s: %s", conversation_id, error)
    except Exception:  # noqa: BLE001
        logger.warning("Summarising conversation %s failed.", conversation_id, exc_info=True)
    return False


async def compact_events(context: UtilityContext, conversation_id: int, focus: str | None) -> AsyncIterator[str]:
    """The event stream of /compact: `compacted` with where the summary is, or `compact_failed`."""
    from app.core.chat import server_sent_event

    try:
        result = await summarise(context, conversation_id, focus or None)
    except SummaryError as error:
        yield server_sent_event("compact_failed", {"detail": str(error)})
        return
    except Exception:  # noqa: BLE001
        logger.warning("Compacting conversation %s failed.", conversation_id, exc_info=True)
        yield server_sent_event("compact_failed", {"detail": "The summary could not be made; see the server log."})
        return
    yield server_sent_event(
        "compacted", {"message_id": result.covered_message_id, "covered": result.covered, "focus": focus or None}
    )


# Summaries started after replies; a reply that is stopped starts one too, so shutdown stops these after the replies.
SUMMARIES: BackgroundWork[bool] = BackgroundWork()


def start_background_summary(context: UtilityContext, conversation_id: int) -> None:
    SUMMARIES.start(summarise_if_needed(context, conversation_id))
