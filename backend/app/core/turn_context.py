"""Context that changes with every message.

It travels as a block at the end of the newest user message in the request and is never stored, so the system prompt
stays the same between messages and its cache holds. It carries the date and time, and the part of a large project
memory that matters for this message.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from app.core.settings import resolve_text
from app.providers.base import HistoryMessage

HEADING = "[Context for this message]"


def zone(session: Session) -> tuple[ZoneInfo, str]:
    name = resolve_text(session, "timezone", None) or "UTC"
    try:
        return ZoneInfo(name), name
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC"), "UTC"


def turn_context(session: Session, recalled_memory: str | None = None, now: datetime | None = None) -> str:
    tz, name = zone(session)
    moment = (now or datetime.now(UTC)).astimezone(tz)
    parts = [f"Date and time: {moment:%A} {moment.day} {moment:%B %Y, %H:%M} ({name})."]
    if recalled_memory:
        parts.append(f"Project memory that matters for this message:\n\n{recalled_memory.strip()}")
    return f"{HEADING}\n" + "\n\n".join(parts)


def with_turn_context(messages: list[HistoryMessage], text: str) -> list[HistoryMessage]:
    """The messages with the context block added to the newest user message."""
    if not text or not messages or messages[-1].role != "user":
        return messages
    last = messages[-1]
    updated = HistoryMessage(
        role="user", content=[*last.content, {"type": "text", "text": text}], provider_raw=last.provider_raw
    )
    return [*messages[:-1], updated]


LARGE_MEMORY_NOTE = (
    "The project memory is larger than fits here. The entries that matter for each message come with that message; "
    "search the rest with search_project (include: memory)."
)


def memory_parts(recalled) -> tuple[str, str | None]:  # type: ignore[no-untyped-def]
    """The memory for part 5 of the system prompt, and the recalled entries for the context block (None when the
    memory goes into part 5 whole)."""
    if recalled.whole:
        return recalled.text, None
    return LARGE_MEMORY_NOTE, recalled.text
