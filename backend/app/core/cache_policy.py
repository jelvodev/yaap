"""How long a reply's cacheable system parts, and the conversation after them, stay in the prompt cache.

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
from app.core.why import Why
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


@dataclass(frozen=True)
class CacheChoice:
    """The cache lifetime chosen for a reply, with the rule that chose it."""

    ttl: CacheTtl
    why: Why


def cache_why(rule: str) -> Why:
    return Why(rule, "cache")


def choose_cache_ttl(facts: CacheFacts) -> CacheChoice:
    if not facts.long_cache:
        return CacheChoice("5m", cache_why("This connection has no long cache, so the five-minute cache is used."))
    if facts.goal:
        rule = "A turn of a goal can wait on long commands and approvals, so the cache lasts an hour."
        return CacheChoice("1h", cache_why(rule))
    if facts.scheduled:
        rule = "A scheduled task runs without anyone at the screen, so the cache lasts an hour."
        return CacheChoice("1h", cache_why(rule))
    if facts.last_reply_at is not None and facts.now - facts.last_reply_at > LONG_PAUSE:
        minutes = int(LONG_PAUSE.total_seconds() // 60)
        rule = (
            f"The previous reply came more than {minutes} minutes ago, so the next message is likely to be slow too: "
            "the cache lasts an hour."
        )
        return CacheChoice("1h", cache_why(rule))
    return CacheChoice("5m", cache_why("A quick follow-up keeps the five-minute cache warm."))


def reply_cache_ttl(
    session: Session, conversation: Conversation, branch: Sequence[Message], spec: ProviderSpec
) -> CacheTtl:
    """The cache TTL for a reply to the last message of branch."""
    return reply_cache_choice(session, conversation, branch, spec).ttl


def reply_cache_choice(
    session: Session, conversation: Conversation, branch: Sequence[Message], spec: ProviderSpec
) -> CacheChoice:
    """The cache lifetime and its reason for a reply to the last message of branch."""
    if not spec.long_cache:
        return choose_cache_ttl(CacheFacts(long_cache=False, now=datetime.now(UTC)))
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
