"""A one-hour prompt cache on API keys: when it is asked for, what it costs, and what traces keep."""

import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.cache_policy import CacheFacts, choose_cache_ttl, reply_cache_ttl
from app.providers.base import AgentRequest, HistoryMessage, SystemPart, TokenUsage
from app.providers.catalog import ANTHROPIC_MODELS, OPENAI_MODELS, calculate_cost
from app.providers.fake_provider import ScriptedResponse
from app.storage.models import Message, RunStep
from tests.test_anthropic_provider import StandInStream, collect, make_provider
from tests.test_chat import add_key, send, start_conversation
from tests.test_delegation import project_conversation
from tests.test_goals import wait_for_goal

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
OPUS = next(model for model in ANTHROPIC_MODELS if model.id == "claude-opus-5-5")


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        (CacheFacts(long_cache=True, goal=True, now=NOW), "1h"),
        (CacheFacts(long_cache=True, scheduled=True, now=NOW), "1h"),
        (CacheFacts(long_cache=True, last_reply_at=NOW - timedelta(minutes=12), now=NOW), "1h"),
        (CacheFacts(long_cache=True, last_reply_at=NOW - timedelta(minutes=2), now=NOW), "5m"),
        (CacheFacts(long_cache=True, last_reply_at=NOW - timedelta(minutes=5), now=NOW), "5m"),
        (CacheFacts(long_cache=True, last_reply_at=None, now=NOW), "5m"),
        # Claude Code, Codex, OpenAI and Ollama keep their own caching, whatever the reply is.
        (CacheFacts(long_cache=False, goal=True, scheduled=True, last_reply_at=NOW - timedelta(hours=1), now=NOW), "5m"),
    ],
)
def test_the_hour_is_chosen_for_goals_scheduled_tasks_and_long_pauses_only(facts, expected):
    assert choose_cache_ttl(facts).ttl == expected


def request_with(ttl: str) -> AgentRequest:
    return AgentRequest(
        model="claude-opus-5-5",
        effort="low",
        system=[SystemPart("Base prompt", cacheable=True), SystemPart("Today is Sunday.")],
        messages=[HistoryMessage("user", [{"type": "text", "text": "Hallo"}])],
        cache_ttl=ttl,  # type: ignore[arg-type]  # parametrised with the two literals
    )


@pytest.mark.parametrize(
    ("ttl", "control"),
    [("1h", {"type": "ephemeral", "ttl": "1h"}), ("5m", {"type": "ephemeral"})],
)
def test_the_anthropic_request_carries_the_ttl_on_the_cacheable_system_part(ttl, control):
    calls: list = []
    collect(make_provider(calls), request_with(ttl))
    system = calls[0]["system"]
    assert system[0]["cache_control"] == control
    assert "cache_control" not in system[1]
    # The automatic breakpoint on the conversation follows the request's lifetime, so the history is kept as long.
    assert calls[0]["cache_control"] == control


def test_hour_writes_are_read_from_the_usage_and_priced_at_twice_the_input(monkeypatch):
    async def final_message(_self):
        return SimpleNamespace(
            content=[],
            stop_reason="end_turn",
            stop_details=None,
            model="claude-opus-5-5",
            usage=SimpleNamespace(
                input_tokens=10,
                output_tokens=0,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=20_000,
                cache_creation=SimpleNamespace(ephemeral_5m_input_tokens=0, ephemeral_1h_input_tokens=20_000),
            ),
        )

    monkeypatch.setattr(StandInStream, "get_final_message", final_message)
    events = collect(make_provider([]), request_with("1h"))
    usage = events[-1].usage
    assert (usage.cache_write_tokens, usage.cache_write_1h_tokens) == (20_000, 20_000)
    # 20,000 x $8 (2x the $4 input) + 10 x $4, per million tokens.
    assert calculate_cost(OPUS, usage) == pytest.approx(0.16004)
    assert calculate_cost(OPUS, replace(usage, cache_write_1h_tokens=0)) == pytest.approx(0.10004)


def test_the_catalog_prices_hour_writes_at_twice_and_five_minute_writes_at_one_and_a_quarter_input():
    for model in ANTHROPIC_MODELS:
        tiers = [model.pricing.standard, *([model.pricing.long_context] if model.pricing.long_context else [])]
        for tier in tiers:
            assert tier.cache_write == pytest.approx(tier.input * 1.25), model.id
            assert tier.cache_write_1h == pytest.approx(tier.input * 2), model.id
    # A vendor without an hour cache prices any hour writes as its ordinary writes.
    luna = OPENAI_MODELS[-1]
    assert luna.pricing.standard.cache_write_1h is None
    usage = TokenUsage(cache_write_tokens=100_000, cache_write_1h_tokens=100_000)
    assert calculate_cost(luna, usage) == pytest.approx(luna.pricing.standard.cache_write / 10)


PREFIX = 20_000  # tokens of tool definitions and cacheable system parts


def goal_prefix_cost(turns: int, calls_per_turn: int, gap: timedelta, ttl: str) -> float:
    """What the cacheable prefix of a fixture goal costs on Claude Opus 5.5: each turn makes its model calls seconds
    apart, the turns are gap apart. A five-minute entry is gone after a gap of more than five minutes and written
    again; an hour entry is written once and read after that."""
    write = TokenUsage(cache_write_tokens=PREFIX, cache_write_1h_tokens=PREFIX if ttl == "1h" else 0)
    read = TokenUsage(cache_read_tokens=PREFIX)
    expires = gap > (timedelta(hours=1) if ttl == "1h" else timedelta(minutes=5))
    writes = turns if expires else 1
    reads = turns * calls_per_turn - writes
    return round(writes * calculate_cost(OPUS, write) + reads * calculate_cost(OPUS, read), 6)


def test_the_hour_pays_for_a_goal_with_pauses_and_not_for_quick_follow_ups():
    paused, quick = timedelta(minutes=12), timedelta(minutes=2)
    # Four turns of five calls, twelve minutes apart: four five-minute writes and 16 reads against one hour write
    # and 19 reads.
    assert goal_prefix_cost(4, 5, paused, "5m") == pytest.approx(0.464)
    assert goal_prefix_cost(4, 5, paused, "1h") == pytest.approx(0.236)
    # Two minutes apart the five-minute entry stays warm: one write either way, and the hour's write costs more.
    assert goal_prefix_cost(4, 5, quick, "5m") == pytest.approx(0.176)
    assert goal_prefix_cost(4, 5, quick, "1h") == pytest.approx(0.236)


@pytest.fixture
def hour_cache(client):
    """The test registry's API-key connection offers the hour, as the real Anthropic API key does."""
    providers = client.app.state.providers
    index = next(i for i, spec in enumerate(providers.specs) if spec.connection == "api_key")
    providers.specs[index] = replace(providers.specs[index], long_cache=True)


def test_a_quick_follow_up_keeps_five_minutes_and_a_long_pause_gets_the_hour(client, fake, hour_cache):
    add_key(client)
    conversation_id = start_conversation(client)
    fake.responses.extend([ScriptedResponse(text="One."), ScriptedResponse(text="Two."), ScriptedResponse(text="Three.")])
    send(client, conversation_id, "First?")
    send(client, conversation_id, "Right after?")
    assert [request.cache_ttl for request in fake.requests] == ["5m", "5m"]

    with client.app.state.session_factory() as session:
        for message in session.scalars(select(Message).where(Message.conversation_id == conversation_id)):
            message.created_at = message.created_at - timedelta(minutes=12)
        session.commit()
    send(client, conversation_id, "After a coffee?")
    assert fake.requests[-1].cache_ttl == "1h"


def test_a_goal_turn_gets_the_hour_and_the_trace_says_so(client, fake, hour_cache):
    _, conversation_id = project_conversation(client)
    fake.responses.append(
        ScriptedResponse(text="Done.", tool_calls=(("finish_goal", {"status": "done", "summary": "Done."}),))
    )
    client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "/goal tidy the readme"})
    wait_for_goal(client, conversation_id, ("done",))

    assert {request.cache_ttl for request in fake.requests} == {"1h"}
    with client.app.state.session_factory() as session:
        summaries = list(session.scalars(select(RunStep.summary).where(RunStep.kind == "model")))
    assert summaries and all((summary or "").startswith("Prompt cache: 1 hour") for summary in summaries)


def test_a_goal_on_claude_code_keeps_its_own_cache(client, fake, hour_cache):
    _, conversation_id = project_conversation(client)
    client.put("/api/v1/settings/anthropic_connection", json={"value": "claude_code"})
    fake.responses.append(
        ScriptedResponse(text="Done.", tool_calls=(("finish_goal", {"status": "done", "summary": "Done."}),))
    )
    client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "/goal tidy the readme"})
    deadline = time.monotonic() + 8
    while not fake.requests and time.monotonic() < deadline:
        time.sleep(0.05)
    assert fake.requests[0].cache_ttl == "5m"


def test_a_scheduled_task_conversation_gets_the_hour(client):
    spec = replace(client.app.state.providers.specs[0], long_cache=True)
    conversation = SimpleNamespace(id=0, scheduled_task_id=7)
    with client.app.state.session_factory() as session:
        assert reply_cache_ttl(session, conversation, [], spec) == "1h"  # type: ignore[arg-type]  # a stand-in
        plain = SimpleNamespace(id=0, scheduled_task_id=None)
        assert reply_cache_ttl(session, plain, [], spec) == "5m"  # type: ignore[arg-type]  # a stand-in


@pytest.mark.parametrize(
    ("facts", "needle"),
    [
        (CacheFacts(long_cache=False, goal=True, now=NOW), "no long cache"),
        (CacheFacts(long_cache=True, goal=True, now=NOW), "goal"),
        (CacheFacts(long_cache=True, scheduled=True, now=NOW), "scheduled task"),
        (CacheFacts(long_cache=True, last_reply_at=NOW - timedelta(minutes=12), now=NOW), "more than 5 minutes"),
        (CacheFacts(long_cache=True, last_reply_at=NOW - timedelta(minutes=2), now=NOW), "quick follow-up"),
        (CacheFacts(long_cache=True, now=NOW), "quick follow-up"),
    ],
)
def test_each_cache_choice_carries_the_rule_of_its_branch(facts, needle):
    why = choose_cache_ttl(facts).why
    assert why.source == "cache" and needle in why.rule
