"""Outgoing checks (core/outgoing_checks.py): a request with a known secret value or a listed pattern does not leave
YAAP, and the refusal names the secret or the pattern, never the value."""

import asyncio
import base64

import httpx
import pytest

from app.a2a import client as a2a_client
from app.a2a.tool import agent_tools
from app.agent.runs import RunConfig, RunRegistry, RunServices
from app.agent.gate import call_tool
from app.agent.tools import tools_for
from app.core.model_policy import ListedPattern, ModelPolicy, PolicySource
from app.core.model_policy_store import resolve_model_policy
from app.core.outgoing_checks import OutgoingRules, checked_provider, request_refusal, text_block, text_refusal
from app.providers.base import AgentRequest, HistoryMessage, ProviderError, SystemPart
from app.providers.fake_provider import FakeProvider, ScriptedResponse
from tests.conftest import VALID_KEY, parse_events
from tests.test_a2a import FakeAgent
from tests.test_chat import start_conversation

SECRET = "hunter2-very-secret"
CARD = r"\b\d{4}-\d{4}-\d{4}-\d{4}\b"
WORK = PolicySource("environment", "Work")
RULES = OutgoingRules(secrets={"DB_PASSWORD": SECRET}, patterns=(ListedPattern(CARD, WORK),))


@pytest.mark.parametrize(
    ("text", "names"),
    [
        ("Nothing special here.", None),
        (f"The password is {SECRET}.", "the secret DB_PASSWORD"),
        (f"Encoded: {base64.b64encode(SECRET.encode()).decode()}", "the secret DB_PASSWORD"),
        ("Pay with 1234-5678-9012-3456 please.", f"the pattern `{CARD}`"),
        # Placeholders are not values, and patterns never match inside them.
        ("Run it with {{secret:DB_PASSWORD}}.", None),
        ("[secret:DB_PASSWORD] was masked.", None),
    ],
)
def test_a_text_with_a_secret_or_a_listed_pattern_is_blocked_by_name(text: str, names: str | None):
    refusal = text_refusal(text, RULES)
    if names is None:
        assert refusal is None
    else:
        assert refusal is not None and names in refusal and "Blocked before it left YAAP" in refusal
        assert SECRET not in refusal and "1234-5678" not in refusal


def test_a_pattern_never_matches_inside_a_placeholder():
    rules = OutgoingRules(patterns=(ListedPattern("DB_PASSWORD", WORK),))
    assert text_refusal("use {{secret:DB_PASSWORD}}", rules) is None
    assert text_refusal("DB_PASSWORD=x", rules) is not None


def request(system: str = "You help.", *blocks: dict) -> AgentRequest:
    return AgentRequest(
        model="claude-opus-5-5",
        effort="medium",
        system=[SystemPart(system)],
        messages=[HistoryMessage("user", list(blocks) or [{"type": "text", "text": "Hello"}])],
    )


@pytest.mark.parametrize(
    ("agent_request", "blocked"),
    [
        (request(), False),
        (request(f"Memory: {SECRET}"), True),
        (request("You help.", {"type": "tool_call", "name": "x", "input": {"note": "4111-1111-1111-1111"}}), True),
        # File data is not text: a base64 image is not searched.
        (request("You help.", {"type": "image", "media_type": "image/png", "data": "1234-5678-9012-3456"}), False),
    ],
)
def test_every_part_of_a_model_request_is_checked(agent_request: AgentRequest, blocked: bool):
    assert (request_refusal(agent_request, RULES) is not None) == blocked


def test_a_blocked_request_never_reaches_the_provider():
    seen: list[AgentRequest] = []
    provider = checked_provider(FakeProvider("claude-opus-5-5", [ScriptedResponse(text="Hi")], seen), RULES)

    async def consume() -> None:
        async for _event in provider.stream(request(f"Memory: {SECRET}")):
            pass

    with pytest.raises(ProviderError, match="the secret DB_PASSWORD"):
        asyncio.run(consume())
    assert seen == []
    assert checked_provider(provider, OutgoingRules()) is provider


def test_a_reply_with_a_secret_value_is_blocked_and_says_which_secret(client, fake):
    client.put("/api/v1/environments/1/credentials/anthropic_api_key", json={"value": VALID_KEY})
    assert client.put("/api/v1/environments/1/secrets/DB_PASSWORD", json={"value": SECRET}).status_code == 200
    fake.responses.append(ScriptedResponse(text="Fine"))
    conversation_id = start_conversation(client)
    response = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": f"It is {SECRET}"})
    message = parse_events(response.text)[-1][1]["message"]
    assert message["status"] == "error" and "the secret DB_PASSWORD" in message["error"]
    assert SECRET not in message["error"] and fake.requests == []
    # A placeholder passes (in a new conversation: the blocked message stays in the first one's history).
    response = client.post(
        f"/api/v1/conversations/{start_conversation(client)}/messages", json={"text": "Use {{secret:DB_PASSWORD}}."}
    )
    assert parse_events(response.text)[-1][1]["message"]["status"] == "complete"


def test_a_listed_pattern_blocks_a_reply_and_a_tool_output(client, fake, tmp_path):
    client.put("/api/v1/environments/1/credentials/anthropic_api_key", json={"value": VALID_KEY})
    saved = client.put("/api/v1/environments/1/settings/model_policy", json={"value": {"patterns": [CARD]}})
    assert saved.status_code == 200, saved.text
    conversation_id = start_conversation(client)
    response = client.post(
        f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Card 1234-5678-9012-3456"}
    )
    message = parse_events(response.text)[-1][1]["message"]
    assert message["status"] == "error" and CARD in message["error"] and "1234-5678" not in message["error"]
    assert fake.requests == []
    # A tool output that matches goes back to the model as a refusal instead.
    (tmp_path / "cards.txt").write_text("4111-1111-1111-1111", encoding="utf-8")
    with client.app.state.session_factory() as session:
        policy = resolve_model_policy(session, 1)
    registry = RunRegistry()
    run = registry.start(
        RunConfig(
            conversation_id=conversation_id,
            environment_id=1,
            blueprint_layers=[],
            tool_names=tools_for(has_project=True, has_attachments=False),
            project_folder=tmp_path,
            project_id=1,
            model_policy=policy,
        ),
        RunServices(),
    )
    output, is_error = asyncio.run(call_tool(registry, run, "read_file", {"path": "cards.txt"}))
    assert is_error and CARD in str(output) and "4111" not in str(output)


def test_an_outgoing_a2a_message_with_a_secret_is_blocked(client, monkeypatch, tmp_path):
    monkeypatch.setattr(a2a_client, "POLL_SECONDS", 0)
    agent = FakeAgent()
    client.app.state.a2a_transport = httpx.MockTransport(agent)
    assert client.put("/api/v1/environments/1/secrets/DB_PASSWORD", json={"value": SECRET}).status_code == 200
    agents = {"helper": {"url": "http://agent.test"}}
    assert client.put("/api/v1/environments/1/settings/a2a_agents", json={"value": agents}).status_code == 200
    state = client.app.state
    with state.session_factory() as session:
        tools = agent_tools(state.session_factory, state.cipher, state.a2a_transport, session, 1, None)
    registry = RunRegistry()
    run = registry.start(
        RunConfig(
            conversation_id=1,
            environment_id=1,
            blueprint_layers=[],
            tool_names=tools_for(has_project=True, has_attachments=False),
            project_folder=tmp_path,
            project_id=1,
            model_policy=ModelPolicy(),
        ),
        RunServices(extra_tools=tools),
    )

    async def scenario() -> tuple[object, bool]:
        async def approve() -> None:
            while True:
                event = await run.events.get()
                if event["event"] == "approval_request":
                    registry.decide(str(event["approval_id"]), "approve")

        approver = asyncio.create_task(approve())
        try:
            return await call_tool(registry, run, "call_agent", {"agent": "helper", "message": f"Key: {SECRET}"})
        finally:
            approver.cancel()

    output, is_error = asyncio.run(scenario())
    assert is_error and "the secret DB_PASSWORD" in str(output) and SECRET not in str(output)
    assert agent.requests == []


@pytest.mark.parametrize(
    ("text", "setting", "layer", "needle"),
    [
        (f"The password is {SECRET}.", None, None, "DB_PASSWORD"),
        ("Pay with 1234-5678-9012-3456 please.", "model_policy", "environment", "Work"),
    ],
)
def test_a_block_carries_its_rule_and_never_the_secret_value(text, setting, layer, needle):
    block = text_block(text, RULES)
    assert block is not None and block.message == text_refusal(text, RULES)
    assert (block.why.source, block.why.setting, block.why.layer) == ("outgoing", setting, layer)
    assert needle in block.why.rule
    assert SECRET not in block.why.rule and "1234-5678" not in block.why.rule
    assert text_block("Nothing special here.", RULES) is None
