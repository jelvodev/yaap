"""The notice after a pause: what continuing a long conversation costs once the prompt cache has expired.

The provider keeps the prompt cache for a limited time after the last call. After a pause longer than that, the next
reply writes the whole context to the cache again, which costs money on a metered connection and time on every
connection. YAAP shows the estimate before the next message is sent and offers to summarise first or to start a new
conversation from a summary. Nothing happens by itself, because a summary loses details of the older turns.

`return_notice` is a pure function of `ReturnFacts`; `conversation_return_notice` gathers those facts.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.cache_policy import CacheFacts, aware, choose_cache_ttl
from app.core.chat_history import load_branch
from app.core.goals import ACTIVE, open_goal
from app.core.settings import resolve_connection
from app.core.summaries import context_used
from app.providers.base import CacheTtl
from app.providers.catalog import ModelPricing
from app.providers.registry import ProviderRegistry
from app.storage.models import Conversation, Message, UsageRecord

# Below this the whole context is cheap to write again, so a notice would only be noise.
CONTEXT_THRESHOLD = 20_000
HOUR_CACHE = timedelta(hours=1)

Billing = Literal["metered", "subscription"]


@dataclass(frozen=True)
class ReturnFacts:
    """What the notice depends on, as plain values."""

    now: datetime
    # When the newest reply's last model call finished.
    last_reply_at: datetime | None
    cache_lifetime: timedelta | None
    # The context the last reply used.
    context_tokens: int | None
    # None when the model is unknown.
    pricing: ModelPricing | None
    # Whether the connection charges per call; False is a subscription.
    metered: bool
    # YAAP's own agent loop, which can summarise; False for Claude Code and Codex.
    own_loop: bool
    # The TTL the next reply's cache write gets, from the cache policy.
    next_ttl: CacheTtl


@dataclass(frozen=True)
class ReturnNotice:
    idle_since: datetime
    cache_lifetime: timedelta
    context_tokens: int
    billing: Billing
    cache_ttl: CacheTtl
    # Estimated dollars; None on a subscription or when the prices are unknown.
    cost: float | None
    can_summarise: bool


def cache_lifetime(spec_lifetime: timedelta | None, long_cache: bool, last_ttl: CacheTtl | None) -> timedelta | None:
    """How long the cache lived after the last reply: an hour when that reply asked for it, else the spec's value."""
    if long_cache and last_ttl == "1h":
        return HOUR_CACHE
    return spec_lifetime


def return_notice(facts: ReturnFacts) -> ReturnNotice | None:
    """The notice to show before the next message, or None when continuing costs little or nothing extra."""
    if facts.last_reply_at is None or facts.cache_lifetime is None or facts.context_tokens is None:
        return None
    if facts.now - facts.last_reply_at <= facts.cache_lifetime or facts.context_tokens <= CONTEXT_THRESHOLD:
        return None
    return ReturnNotice(
        idle_since=facts.last_reply_at,
        cache_lifetime=facts.cache_lifetime,
        context_tokens=facts.context_tokens,
        billing="metered" if facts.metered else "subscription",
        cache_ttl=facts.next_ttl,
        cost=rewrite_cost(facts),
        can_summarise=facts.own_loop,
    )


def rewrite_cost(facts: ReturnFacts) -> float | None:
    """The price of writing the whole context to the cache again, or None where no price applies."""
    if not facts.metered or facts.pricing is None or facts.context_tokens is None:
        return None
    tier = facts.pricing.tier_for(facts.context_tokens)
    price = tier.cache_write_1h if facts.next_ttl == "1h" and tier.cache_write_1h is not None else tier.cache_write
    return round(facts.context_tokens * price / 1_000_000, 4)


def conversation_return_notice(
    session: Session, providers: ProviderRegistry, conversation: Conversation, now: datetime
) -> ReturnNotice | None:
    """The notice for a conversation at this moment; now is an aware UTC time."""
    connection = resolve_connection(session, conversation.provider, conversation.environment_id)
    spec = providers.find_provider(conversation.provider, connection)
    if spec is None:
        return None
    model = providers.find_model(conversation.model)
    branch = load_branch(session, conversation.id, conversation.head_message_id)
    replies = [message for message in branch if message.role == "assistant"]
    last_ttl = last_reply_ttl(session, conversation, branch) if spec.long_cache else None
    facts = ReturnFacts(
        now=now,
        last_reply_at=last_main_reply_at(session, conversation),
        cache_lifetime=cache_lifetime(spec.cache_lifetime, spec.long_cache, last_ttl),
        context_tokens=context_used(session, conversation),
        pricing=model.pricing if model else None,
        metered=spec.metered,
        own_loop=not spec.compacts_itself,
        next_ttl=choose_cache_ttl(
            CacheFacts(
                long_cache=spec.long_cache,
                now=now,
                goal=has_active_goal(session, conversation),
                scheduled=conversation.scheduled_task_id is not None,
                last_reply_at=aware(replies[-1].created_at) if replies else None,
            )
        ).ttl,
    )
    return return_notice(facts)


def last_main_reply_at(session: Session, conversation: Conversation) -> datetime | None:
    """When the newest reply's last model call finished: its usage record is written then."""
    created_at = session.scalar(
        select(UsageRecord.created_at)
        .where(UsageRecord.conversation_id == conversation.id, UsageRecord.purpose == "main")
        .order_by(UsageRecord.id.desc())
    )
    return aware(created_at) if created_at is not None else None


def has_active_goal(session: Session, conversation: Conversation) -> bool:
    goal = open_goal(session, conversation.id)
    return goal is not None and goal.status == ACTIVE


def last_reply_ttl(session: Session, conversation: Conversation, branch: list[Message]) -> CacheTtl | None:
    """The TTL the newest reply was cached with, decided as it was then: from the user message it answered.

    That message's time is when the pause before the reply ended; the reply before it is when the previous one
    finished.
    """
    positions = [index for index, message in enumerate(branch) if message.role == "assistant"]
    if not positions:
        return None
    last = positions[-1]
    asked = next((message for message in reversed(branch[:last]) if message.role == "user"), branch[last])
    previous = branch[positions[-2]] if len(positions) > 1 else None
    facts = CacheFacts(
        long_cache=True,
        now=aware(asked.created_at),
        goal=has_active_goal(session, conversation),
        scheduled=conversation.scheduled_task_id is not None,
        last_reply_at=aware(previous.created_at) if previous else None,
    )
    return choose_cache_ttl(facts).ttl
