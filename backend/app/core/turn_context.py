"""Context that changes with every message.

It travels as a block at the end of the newest user message in the request and is never stored, so the system prompt
stays the same between messages and its cache holds. It carries the date and time, and the part of a large project
memory that matters for this message.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from app.core.settings import resolve_text
from app.projects.current_state import without_state
from app.projects.memory import Recall
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


STATE_LABEL = "### Where this project stands"


def memory_parts(recalled: Recall) -> tuple[str, str | None]:
    """The memory for part 5 of the system prompt, and the recalled entries for the context block (None when the
    memory goes into part 5 whole). The Current state block leads part 5 on every turn, whatever the memory's size,
    so it stays in the cached prefix."""
    if recalled.whole:
        memory, recalled_text = without_state(recalled.text), None
    else:
        memory, recalled_text = LARGE_MEMORY_NOTE, recalled.text
    if not recalled.state:
        return memory, recalled_text
    return f"{STATE_LABEL}\n\n{recalled.state}\n\n{memory.strip()}".strip(), recalled_text
