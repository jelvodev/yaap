from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.core.return_notice import (
    CONTEXT_THRESHOLD,
    HOUR_CACHE,
    ReturnFacts,
    cache_lifetime,
    conversation_return_notice,
    return_notice,
)
from app.providers.catalog import ANTHROPIC_MODELS
from app.providers.registry import default_provider_registry
from app.storage.models import Conversation, UsageRecord
from tests.test_chat import add_key

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
MINUTES = timedelta(minutes=5)


def pricing_of(model_id: str):
    return next(model.pricing for model in ANTHROPIC_MODELS if model.id == model_id)


def facts(**changes) -> ReturnFacts:
    base = ReturnFacts(
        now=NOW,
        last_reply_at=NOW - timedelta(minutes=30),
        cache_lifetime=MINUTES,
        context_tokens=100_000,
        pricing=pricing_of("claude-opus-5-5"),
        metered=True,
        own_loop=True,
        next_ttl="5m",
    )
    return replace(base, **changes)


@pytest.mark.parametrize(
    ("changes", "cost"),
    [
        ({}, 0.50),
        ({"next_ttl": "1h"}, 0.80),
        ({"pricing": pricing_of("claude-haiku-5-5"), "context_tokens": 100_000}, 0.0125),
        ({"pricing": pricing_of("claude-haiku-5-5"), "context_tokens": 200_000}, 0.125),
        ({"pricing": pricing_of("claude-haiku-5-5"), "context_tokens": 200_000, "next_ttl": "1h"}, 0.20),
    ],
)
def test_the_estimate_is_a_cache_write_of_the_whole_context(changes, cost):
    notice = return_notice(facts(**changes))
    assert notice is not None and notice.cost == cost


@pytest.mark.parametrize(
    ("changes", "billing", "cost"),
    [
        ({"metered": False}, "subscription", None),
        ({"pricing": None}, "metered", None),
        ({"metered": False, "pricing": None}, "subscription", None),
    ],
)
def test_no_cost_on_a_subscription_or_with_unknown_prices(changes, billing, cost):
    notice = return_notice(facts(**changes))
    assert notice is not None and (notice.billing, notice.cost) == (billing, cost)


@pytest.mark.parametrize(
    "changes",
    [
        {"last_reply_at": NOW - timedelta(minutes=2)},
        {"last_reply_at": NOW - MINUTES},
        {"context_tokens": CONTEXT_THRESHOLD},
        {"context_tokens": 500},
        {"cache_lifetime": None},
        {"last_reply_at": None},
        {"context_tokens": None},
    ],
)
def test_no_notice_while_the_cache_lives_or_the_context_is_small_or_unknown(changes):
    assert return_notice(facts(**changes)) is None


@pytest.mark.parametrize("own_loop", [True, False])
def test_summarising_is_offered_only_for_the_own_agent_loop(own_loop):
    notice = return_notice(facts(own_loop=own_loop))
    assert notice is not None and notice.can_summarise is own_loop


def test_the_notice_carries_the_facts_it_was_made_from():
    notice = return_notice(facts(next_ttl="1h"))
    assert notice is not None
    assert (notice.idle_since, notice.cache_lifetime, notice.context_tokens, notice.cache_ttl) == (
        NOW - timedelta(minutes=30), MINUTES, 100_000, "1h",
    )  # fmt: skip


@pytest.mark.parametrize(
    ("spec_lifetime", "long_cache", "last_ttl", "expected"),
    [
        (MINUTES, True, "1h", HOUR_CACHE),
        (MINUTES, True, "5m", MINUTES),
        (MINUTES, True, None, MINUTES),
        (MINUTES, False, "1h", MINUTES),
        (None, False, "1h", None),
        (None, True, "5m", None),
    ],
)
def test_the_cache_lifetime_is_an_hour_only_when_the_last_reply_asked_for_it(
    spec_lifetime, long_cache, last_ttl, expected
):
    assert cache_lifetime(spec_lifetime, long_cache, last_ttl) == expected


def test_a_conversation_idle_past_the_cache_lifetime_gets_a_notice(client):
    add_key(client)
    conversation_id = client.post(
        "/api/v1/environments/1/conversations", json={"model": "claude-opus-5-5", "effort": "medium"}
    ).json()["id"]
    with client.app.state.session_factory() as session:
        session.add(
            UsageRecord(
                environment_id=1, conversation_id=conversation_id, provider="anthropic", model="claude-opus-5-5",
                purpose="main", context_tokens=50_000, created_at=NOW - timedelta(minutes=10),
            )
        )  # fmt: skip
        session.commit()
        from app.storage.models import Conversation

        conversation = session.get(Conversation, conversation_id)
        assert conversation is not None
        notice = conversation_return_notice(session, default_provider_registry(), conversation, NOW)
    assert notice is not None
    assert (notice.context_tokens, notice.billing, notice.cache_ttl, notice.can_summarise) == (
        50_000, "metered", "5m", True,
    )  # fmt: skip
    assert notice.cost == 0.25
