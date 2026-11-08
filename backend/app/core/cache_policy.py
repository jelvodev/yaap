"""How long a reply's cacheable system parts stay in the prompt cache.

A five-minute cache entry is refreshed by every read, so a conversation whose model calls start less than five
minutes apart keeps it warm for nothing extra. An hour costs more to write (2x input instead of 1.25x on Anthropic)
and pays only when the next call that shares the prefix comes after five minutes and within the hour. The rule
guesses that from what the reply is:

- a turn of a goal: goal turns follow each other, but a turn can wait on long commands and approvals;
- a scheduled task: it runs without anyone at the screen, and a task that repeats within the hour reuses the prefix;
- a conversation with a long pause: the previous reply ended more than LONG_PAUSE before this message, so the user
  works at that pace and the next message is likely to come after five minutes too.

A quick follow-up keeps the five-minute cache. Only a connection that offers the hour (`ProviderSpec.long_cache`,
the Anthropic API key) gets it; Claude Code and Codex manage their own cache.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.goals import ACTIVE, open_goal
from app.providers.base import CacheTtl
from app.providers.registry import ProviderSpec
from app.storage.models import Conversation, Message

LONG_PAUSE = timedelta(minutes=5)


@dataclass(frozen=True)
class CacheFacts:
    """What the choice depends on, as plain values."""

    long_cache: bool
    now: datetime
    goal: bool = False
    scheduled: bool = False
    # When the previous reply in the branch was stored; None for the first message.
    last_reply_at: datetime | None = None


def choose_cache_ttl(facts: CacheFacts) -> CacheTtl:
    if not facts.long_cache:
        return "5m"
    if facts.goal or facts.scheduled:
        return "1h"
    if facts.last_reply_at is not None and facts.now - facts.last_reply_at > LONG_PAUSE:
        return "1h"
    return "5m"


def reply_cache_ttl(
    session: Session, conversation: Conversation, branch: Sequence[Message], spec: ProviderSpec
) -> CacheTtl:
    """The cache TTL for a reply to the last message of branch."""
    if not spec.long_cache:
        return "5m"
    goal = open_goal(session, conversation.id)
    replies = [message for message in branch if message.role == "assistant"]
    facts = CacheFacts(
        long_cache=True,
        goal=goal is not None and goal.status == ACTIVE,
        scheduled=conversation.scheduled_task_id is not None,
        last_reply_at=aware(replies[-1].created_at) if replies else None,
        now=datetime.now(UTC),
    )
    return choose_cache_ttl(facts)


def aware(moment: datetime) -> datetime:
    """A stored time with its zone; SQLite may give it back without one, always in UTC."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
