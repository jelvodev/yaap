"""The model policy (core/model_policy.py): which connections, models and A2A agents an environment or project may use,
enforced where every model request and the Workers listing pass (confirm_pick) and in the permission decision."""

import asyncio
from collections.abc import Iterator
from dataclasses import replace

import httpx
import pytest
from sqlalchemy.orm import Session

from app.a2a import client as a2a_client
from app.a2a.tool import agent_tools
from app.agent.permissions import RunFacts, ToolFacts, decide
from app.agent.runs import RunConfig, RunRegistry, RunServices
from app.agent.gate import call_tool
from app.agent.tools import tools_for
from app.agent.workers import list_workers
from app.core.model_choice import ForReply, ForSubRun, ForUtility, ModelRequest, PolicyForbids, resolve_provider
from app.core.model_policy import (
    LevelPolicy,
    ModelPolicy,
    PolicyChoices,
    PolicySource,
    PolicyTarget,
    agent_refusal,
    check_policy,
    model_refusal,
    parse_level,
    widening_problem,
)
from app.core.model_policy_store import resolve_model_policy
from app.core.settings import SETTING_DEFINITIONS, store_setting
from app.providers.catalog import ANTHROPIC_MODELS, OPENAI_MODELS, ModelInfo
from app.providers.registry import ProviderRegistry, ProviderSpec
from tests.test_a2a import FakeAgent
from tests.test_model_choice import CONVERSATION, ENVIRONMENT, add_key, fake_provider

WORK = PolicySource("environment", "Work")
SITE = PolicySource("project", "Site")
LLAMA = replace(ANTHROPIC_MODELS[2], provider="ollama", id="llama4", display_name="llama4")
OPUS = ANTHROPIC_MODELS[0]


def registry() -> ProviderRegistry:
    """The connections of a real installation, with a local model on Ollama and Opus marked for business data."""
    specs = [
        ProviderSpec("anthropic", "Anthropic", "anthropic_api_key", fake_provider, runs_agents=True),
        ProviderSpec("anthropic", "Claude Code", None, fake_provider, "claude_code", runs_agents=True, metered=False),
        ProviderSpec("openai", "OpenAI", "openai_api_key", fake_provider, runs_agents=True),
        ProviderSpec("openai", "Codex", None, fake_provider, "codex", runs_agents=True, metered=False),
        ProviderSpec("ollama", "Ollama", None, fake_provider, "local", runs_agents=True, metered=False, local=True),
    ]
    models: list[ModelInfo] = [replace(OPUS, business_data=True), *ANTHROPIC_MODELS[1:], *OPENAI_MODELS, LLAMA]
    return ProviderRegistry(specs, models)


def target(model: str = "claude-opus-5-5", connection: str = "anthropic:api_key", **marks: bool) -> PolicyTarget:
    return PolicyTarget(model, model, connection, connection, **marks)


def policy(*levels: tuple[PolicySource, LevelPolicy]) -> ModelPolicy:
    return ModelPolicy(levels)


LOCAL_ONLY = LevelPolicy(local_only=True)


@pytest.mark.parametrize(
    ("rules", "request_target", "refused_by"),
    [
        # No policy: everything is allowed.
        (policy(), target(), None),
        # Only local models allows Ollama and nothing else, not even a subscription.
        (policy((WORK, LOCAL_ONLY)), target("llama4", "ollama:local", local=True), None),
        (policy((WORK, LOCAL_ONLY)), target(), "only local models are allowed"),
        (policy((WORK, LOCAL_ONLY)), target("claude-sonnet-5-5", "anthropic:claude_code"), "only local models"),
        (policy((WORK, LOCAL_ONLY)), target("gpt-6.1-sol", "openai:codex"), "only local models"),
        # Allowed connections and models.
        (policy((WORK, LevelPolicy(connections=frozenset({"anthropic:api_key"})))), target(), None),
        (
            policy((WORK, LevelPolicy(connections=frozenset({"anthropic:api_key"})))),
            target("claude-opus-5-5", "anthropic:claude_code"),
            "the connection anthropic:claude_code (anthropic:claude_code) is not allowed",
        ),
        (policy((WORK, LevelPolicy(models=frozenset({"claude-opus-5-5"})))), target(), None),
        (
            policy((WORK, LevelPolicy(models=frozenset({"claude-opus-5-5"})))),
            target("gpt-6.1-sol"),
            "the model gpt-6.1-sol is not allowed (allowed: claude-opus-5-5)",
        ),
        # The business data requirement: the catalog's mark, or a local model.
        (policy((WORK, LevelPolicy(business_data=True))), target(business_data=True), None),
        (policy((WORK, LevelPolicy(business_data=True))), target("llama4", "ollama:local", local=True), None),
        (
            policy((WORK, LevelPolicy(business_data=True))),
            target(),
            "models must be marked for business data processing",
        ),
        # A project narrows: both levels apply, and the refusal names the level whose rule was broken.
        (
            policy((WORK, LevelPolicy(models=frozenset({"claude-opus-5-5", "gpt-6.1-sol"}))), (SITE, LOCAL_ONLY)),
            target("gpt-6.1-sol"),
            "the project Site: only local models",
        ),
        (
            policy((WORK, LevelPolicy(models=frozenset({"claude-opus-5-5"}))), (SITE, LevelPolicy())),
            target("gpt-6.1-sol"),
            "the environment Work: the model gpt-6.1-sol",
        ),
    ],
)
def test_the_policy_refuses_what_a_level_does_not_allow(rules: ModelPolicy, request_target: PolicyTarget, refused_by):
    refusal = model_refusal(rules, request_target)
    if refused_by is None:
        assert refusal is None
    else:
        assert refusal is not None and refused_by in refusal and refusal.startswith("Refused by the model policy of")


@pytest.mark.parametrize(
    ("rules", "agent", "refused"),
    [
        (policy(), "helper", False),
        (policy((WORK, LevelPolicy(agents=frozenset({"helper"})))), "helper", False),
        (policy((WORK, LevelPolicy(agents=frozenset({"helper"})))), "other", True),
        (policy((WORK, LevelPolicy(agents=frozenset()))), "helper", True),
        (
            policy((WORK, LevelPolicy(agents=frozenset({"helper"}))), (SITE, LevelPolicy(agents=frozenset()))),
            "helper",
            True,
        ),
    ],
)
def test_a2a_agents_outside_the_list_are_refused(rules: ModelPolicy, agent: str, refused: bool):
    assert (agent_refusal(rules, agent) is not None) == refused
    tool = ToolFacts("call_agent", "ask", f"Ask agent {agent}")
    decision = decide(tool, {"agent": agent}, RunFacts(model_policy=rules))
    assert decision.verdict == ("refuse" if refused else "ask")
    if refused:
        assert decision.reason == "model_policy" and "the A2A agent" in decision.message


CHOICES = PolicyChoices(
    connections={"anthropic:api_key": False, "ollama:local": True},
    local_models=frozenset({"llama4"}),
    models=frozenset({"claude-opus-5-5", "llama4"}),
)


@pytest.mark.parametrize(
    ("environment", "project", "problem"),
    [
        ({}, {"models": ["claude-opus-5-5"]}, None),
        ({"connections": ["anthropic:api_key"]}, {"connections": []}, None),
        ({"connections": ["anthropic:api_key"]}, {"connections": ["ollama:local"]}, "not ollama:local"),
        ({"models": ["claude-opus-5-5"]}, {"models": "local"}, None),
        ({"models": ["claude-opus-5-5"]}, {"models": ["claude-opus-5-5", "llama4"]}, "not llama4"),
        ({"models": "local"}, {"models": ["llama4"]}, None),
        ({"models": "local"}, {"models": ["claude-opus-5-5"]}, "only local models"),
        ({"business_data": True}, {"business_data": False}, "cannot drop that"),
        ({"business_data": True}, {}, None),
        ({"agents": ["helper"]}, {"agents": ["helper", "other"]}, "not other"),
        # More patterns narrow.
        ({"patterns": ["a+b"]}, {"patterns": ["c+d"]}, None),
    ],
)
def test_a_project_can_narrow_and_cannot_widen(environment: dict, project: dict, problem: str | None):
    found = widening_problem(parse_level(environment), parse_level(project), "Work", CHOICES)
    if problem is None:
        assert found is None
    else:
        assert found is not None and problem in found and "can only narrow the policy of the environment Work" in found


@pytest.mark.parametrize(
    ("value", "problem"),
    [
        ({}, None),
        ({"connections": ["ollama:local"], "models": "local", "business_data": True, "agents": ["a-1"]}, None),
        ({"patterns": [r"\bNL\d{2}[A-Z]{4}\d{10}\b"]}, None),
        ({"patterns": ["(unclosed"]}, "not a valid regular expression"),
        ({"patterns": [".*"]}, "matches empty text"),
        ({"connections": ["anthropic:telepathy"]}, "Unknown connections"),
        ({"models": ["no-such-model"]}, "Unknown models"),
        ({"models": "remote"}, "Models"),
        ({"business_data": "yes"}, "true or false"),
        ({"agents": ["bad name"]}, "not a valid name"),
        ({"colour": "red"}, "Unknown keys"),
    ],
)
def test_a_policy_is_validated_when_saved(value: dict, problem: str | None):
    found = check_policy(value, registry())
    assert found is None if problem is None else (found is not None and problem in found)


# Enforcement for every caller, through resolve_provider and the Workers listing.


@pytest.fixture
def session(client) -> Iterator[Session]:
    with client.app.state.session_factory() as opened:
        yield opened


def environment_policy(session: Session, value: dict) -> None:
    store_setting(session, SETTING_DEFINITIONS["model_policy"], ENVIRONMENT, value)


FORBIDDEN_FOR_EVERY_CALLER: list[ModelRequest] = [
    ForReply("anthropic", "claude-opus-5-5", "medium", "Balanced"),
    ForSubRun(**CONVERSATION),
    ForSubRun(**CONVERSATION, worker="Deep"),
    ForSubRun(**CONVERSATION, worker="claude_code"),
    ForSubRun(**CONVERSATION, worker="codex"),
    ForUtility("Balanced"),
]


@pytest.mark.parametrize("request_", FORBIDDEN_FOR_EVERY_CALLER)
def test_a_forbidden_model_is_refused_for_every_caller(client, session, request_: ModelRequest):
    add_key(client, session, "anthropic")
    add_key(client, session, "openai")
    environment_policy(session, {"models": "local"})
    with pytest.raises(PolicyForbids, match="Refused by the model policy of the environment .*only local models"):
        resolve_provider(session, registry(), ENVIRONMENT, request_)


def test_only_local_models_still_serves_ollama(client, session):
    environment_policy(session, {"models": "local"})
    choice = resolve_provider(session, registry(), ENVIRONMENT, ForReply("ollama", "llama4", "medium"))
    assert choice.connection == "local"


def test_the_business_data_requirement_allows_only_marked_models(client, session):
    add_key(client, session, "anthropic")
    environment_policy(session, {"business_data": True})
    assert resolve_provider(session, registry(), ENVIRONMENT, ForReply("anthropic", "claude-opus-5-5", "medium")).model
    with pytest.raises(PolicyForbids, match="marked for business data processing"):
        resolve_provider(session, registry(), ENVIRONMENT, ForReply("anthropic", "claude-sonnet-5-5", "medium"))


def test_the_utility_model_falls_back_to_an_allowed_utility_model_or_skips(client, session):
    add_key(client, session, "anthropic")
    add_key(client, session, "openai")
    # The preset's utility model (Haiku by the setting's default) is not allowed; the setting names an allowed one.
    environment_policy(session, {"models": ["gpt-6-luna", "claude-opus-5-5"]})
    store_setting(session, SETTING_DEFINITIONS["utility_model"], ENVIRONMENT, "gpt-6-luna")
    from app.presets.service import find_preset_by_name

    preset = find_preset_by_name(session, ENVIRONMENT, "Balanced")
    assert preset is not None
    preset.utility_model, preset.utility_effort = "claude-sonnet-5-5", "low"
    session.commit()
    assert resolve_provider(session, registry(), ENVIRONMENT, ForUtility("Balanced")).model == "gpt-6-luna"
    # Without an allowed utility model the task is refused, with the preset model's reason.
    environment_policy(session, {"models": ["claude-opus-5-5"]})
    with pytest.raises(PolicyForbids, match="the model Claude Sonnet 5.5 is not allowed"):
        resolve_provider(session, registry(), ENVIRONMENT, ForUtility("Balanced"))


def test_a_project_policy_applies_only_to_its_project(client, session):
    add_key(client, session, "anthropic")
    project = client.post("/api/v1/environments/1/projects", json={"name": "Site"}).json()
    url = f"/api/v1/projects/{project['id']}/settings/model_policy"
    narrowed = client.put(url, json={"value": {"models": "local"}})
    assert narrowed.status_code == 200, narrowed.text
    request_ = ForReply("anthropic", "claude-opus-5-5", "medium")
    assert resolve_provider(session, registry(), ENVIRONMENT, request_).model == "claude-opus-5-5"
    with pytest.raises(PolicyForbids, match="the project Site: only local models"):
        resolve_provider(session, registry(), ENVIRONMENT, replace(request_, project_id=project["id"]))


def test_the_settings_refuse_a_project_policy_that_widens_and_an_invalid_pattern(client):
    project = client.post("/api/v1/environments/1/projects", json={"name": "Site"}).json()
    url = f"/api/v1/projects/{project['id']}/settings/model_policy"
    saved = client.put("/api/v1/environments/1/settings/model_policy", json={"value": {"models": ["claude-opus-5-5"]}})
    assert saved.status_code == 200, saved.text
    widened = client.put(url, json={"value": {"models": ["claude-opus-5-5", "claude-haiku-5-5"]}})
    assert widened.status_code == 422 and "can only narrow" in widened.json()["detail"]
    assert client.put(url, json={"value": {"models": "local", "patterns": ["[0-9]{16}"]}}).status_code == 200
    invalid = client.put(url, json={"value": {"patterns": ["(unclosed"]}})
    assert invalid.status_code == 422 and "not a valid regular expression" in invalid.json()["detail"]


def test_the_workers_listing_marks_forbidden_workers_not_allowed(client, session):
    add_key(client, session, "anthropic")
    environment_policy(session, {"connections": ["anthropic:api_key"]})
    workers = {worker.name: worker for worker in list_workers(session, registry(), ENVIRONMENT)}
    assert workers["Balanced"].allowed and workers["Balanced"].available
    for name in ("claude_code", "codex"):
        assert not workers[name].allowed and not workers[name].available
        assert "Refused by the model policy of the environment" in (workers[name].unavailable_reason or "")


def test_call_agent_to_an_agent_outside_the_policy_is_refused_before_asking(client, monkeypatch, tmp_path):
    monkeypatch.setattr(a2a_client, "POLL_SECONDS", 0)
    agent = FakeAgent()
    client.app.state.a2a_transport = httpx.MockTransport(agent)
    agents = {"helper": {"url": "http://agent.test", "description": "Knows the news."}}
    assert client.put("/api/v1/environments/1/settings/a2a_agents", json={"value": agents}).status_code == 200
    saved = client.put("/api/v1/environments/1/settings/model_policy", json={"value": {"agents": ["someone-else"]}})
    assert saved.status_code == 200, saved.text
    state = client.app.state
    with state.session_factory() as session:
        tools = agent_tools(state.session_factory, state.cipher, state.a2a_transport, session, 1, None)
        rules = resolve_model_policy(session, 1)
    registry_ = RunRegistry()
    run = registry_.start(
        RunConfig(
            conversation_id=1,
            environment_id=1,
            blueprint_layers=[],
            tool_names=tools_for(has_project=True, has_attachments=False),
            project_folder=tmp_path,
            project_id=1,
            model_policy=rules,
        ),
        RunServices(extra_tools=tools),
    )
    output, is_error = asyncio.run(call_tool(registry_, run, "call_agent", {"agent": "helper", "message": "Hi"}))
    assert is_error and "the A2A agent helper is not allowed" in str(output)
    assert agent.requests == [] and run.events.empty()
