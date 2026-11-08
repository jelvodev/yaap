"""One way to choose provider, model and key (core/model_choice.py): table-driven cases per caller."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from app.core.model_choice import (
    BudgetReached,
    CannotRunAgents,
    ConnectionUnavailable,
    ForReply,
    ForSubRun,
    ForUtility,
    MissingKey,
    ModelChoice,
    ModelChoiceError,
    ModelRequest,
    UnknownModel,
    UnknownWorker,
    UnusableKey,
    WrongVendor,
    read_key,
    resolve_provider,
)
from app.core.settings import SETTING_DEFINITIONS, store_setting
from app.credentials.store import save_credential
from app.credentials.types import ANTHROPIC_API_KEY, CredentialTestResult
from app.presets.service import find_preset_by_name
from app.providers.catalog import ANTHROPIC_MODELS, OPENAI_MODELS
from app.providers.fake_provider import FakeProvider
from app.providers.registry import ProviderRegistry, ProviderSpec
from app.storage.models import UsageRecord

ENVIRONMENT = 1
KINDS = {"anthropic": "anthropic_api_key", "openai": "openai_api_key"}


def fake_provider(_api_key: str | None) -> FakeProvider:
    return FakeProvider("claude-opus-5-5", [], [])


def registry(api_keys_run_agents: bool = True, codex: bool = True) -> ProviderRegistry:
    specs = [
        ProviderSpec("anthropic", "Anthropic", "anthropic_api_key", fake_provider, runs_agents=api_keys_run_agents),
        ProviderSpec("anthropic", "Claude Code", None, fake_provider, "claude_code", runs_agents=True, metered=False),
        ProviderSpec("openai", "OpenAI", "openai_api_key", fake_provider, runs_agents=api_keys_run_agents),
    ]
    if codex:
        specs.append(ProviderSpec("openai", "Codex", None, fake_provider, "codex", runs_agents=True, metered=False))
    return ProviderRegistry(specs, [*ANTHROPIC_MODELS, *OPENAI_MODELS])


@pytest.fixture
def session(client) -> Iterator[Session]:
    with client.app.state.session_factory() as opened:
        yield opened


def add_key(client, session: Session, provider: str, value: str = "sk-test-valid-key-1234") -> None:
    ok = CredentialTestResult(True, "The key works.")
    save_credential(session, client.app.state.cipher, ENVIRONMENT, KINDS[provider], value, ok)


def setting(session: Session, key: str, value: object) -> None:
    store_setting(session, SETTING_DEFINITIONS[key], None, value)


def change_preset(session: Session, name: str, **fields: object) -> None:
    preset = find_preset_by_name(session, ENVIRONMENT, name)
    assert preset is not None
    for field_name, value in fields.items():
        setattr(preset, field_name, value)
    session.commit()


@dataclass(frozen=True)
class Expected:
    model: str
    effort: str
    connection: str
    billing: str = "metered"
    worker: str | None = None
    worker_label: str | None = None


def check(choice: ModelChoice, expected: Expected) -> None:
    assert (choice.model, choice.effort, choice.connection, choice.billing) == (
        expected.model,
        expected.effort,
        expected.connection,
        expected.billing,
    )
    assert (choice.worker, choice.worker_label) == (expected.worker, expected.worker_label)
    assert choice.spec.connection == choice.connection
    if choice.spec.credential_kind is None:
        assert choice.key is None
    else:
        assert choice.key is not None and choice.key.kind == choice.spec.credential_kind


CONVERSATION = {"preset": "Balanced", "model": "claude-opus-5-5", "effort": "medium"}


@pytest.mark.parametrize(
    ("request_", "expected"),
    [
        # The default worker: the conversation's preset's worker model.
        (ForSubRun(**CONVERSATION), Expected("claude-sonnet-5-5", "low", "api_key")),
        (ForSubRun(**CONVERSATION, role="main"), Expected("claude-opus-5-5", "medium", "api_key")),
        # A preset without a worker model: the default preset's worker model.
        (ForSubRun("Fast", "claude-sonnet-5-5", "low"), Expected("claude-sonnet-5-5", "low", "api_key")),
        # An agent with its own preset runs on that preset's main model, unless it asks for the worker role.
        (ForSubRun(**CONVERSATION, agent_preset="Cheap"), Expected("claude-haiku-5-5", "low", "api_key")),
        (
            ForSubRun(**CONVERSATION, role="main", agent_preset="Cheap", agent_role="worker"),
            Expected("claude-sonnet-5-5", "low", "api_key"),
        ),
        # A named preset: its worker model, its main model with role main or without a worker model.
        (
            ForSubRun(**CONVERSATION, worker="Deep"),
            Expected("claude-sonnet-5-5", "medium", "api_key", "metered", "Deep", "Deep"),
        ),
        (
            ForSubRun(**CONVERSATION, worker="deep", role="main"),
            Expected("claude-opus-5-5", "high", "api_key", "metered", "Deep", "Deep"),
        ),
        (
            ForSubRun(**CONVERSATION, worker="Fast"),
            Expected("claude-sonnet-5-5", "low", "api_key", "metered", "Fast", "Fast"),
        ),
        # The CLI workers start their own CLI on the subscription, whatever connection the environment uses.
        (
            ForSubRun(**CONVERSATION, worker="claude_code"),
            Expected("claude-sonnet-5-5", "medium", "claude_code", "subscription", "claude_code", "Claude Code"),
        ),
        (
            ForSubRun(**CONVERSATION, worker="codex"),
            Expected("gpt-6.1-sol", "medium", "codex", "subscription", "codex", "Codex"),
        ),
    ],
)
def test_a_sub_run_gets_the_model_its_request_names(client, session, request_: ForSubRun, expected: Expected):
    add_key(client, session, "anthropic")
    check(resolve_provider(session, registry(), ENVIRONMENT, request_), expected)


def test_a_sub_run_falls_back_to_the_conversations_model_when_no_preset_has_a_worker(client, session):
    add_key(client, session, "openai")
    change_preset(session, "Balanced", worker_model=None, worker_effort=None)
    change_preset(session, "Deep", worker_model=None, worker_effort=None)
    choice = resolve_provider(session, registry(), ENVIRONMENT, ForSubRun("Balanced", "gpt-6.1-terra", "high"))
    check(choice, Expected("gpt-6.1-terra", "high", "api_key"))


@pytest.mark.parametrize(
    ("anthropic_connection", "expected"),
    [
        ("api_key", Expected("claude-opus-5-5", "medium", "api_key")),
        # A subscription connection needs no key and is not metered.
        ("claude_code", Expected("claude-opus-5-5", "medium", "claude_code", "subscription")),
    ],
)
def test_a_reply_uses_the_connection_the_environment_chose(client, session, anthropic_connection, expected):
    add_key(client, session, "anthropic")
    setting(session, "anthropic_connection", anthropic_connection)
    request = ForReply("anthropic", "claude-opus-5-5", "medium", "Balanced")
    choice = resolve_provider(session, registry(), ENVIRONMENT, request)
    check(choice, expected)
    assert choice.preset == "Balanced" and choice.purpose == "reply"


@pytest.mark.parametrize(
    ("preset_changes", "utility_setting", "request_", "expected"),
    [
        # No preset has a utility model: the utility_model setting at low effort.
        ({}, None, ForUtility("Balanced"), Expected("claude-haiku-5-5", "low", "api_key")),
        ({}, "claude-sonnet-5-5", ForUtility("Balanced"), Expected("claude-sonnet-5-5", "low", "api_key")),
        ({}, "gpt-6-luna", ForUtility(), Expected("gpt-6-luna", "low", "api_key")),
        # The preset's own utility model, then the default preset's.
        (
            {"Fast": {"utility_model": "claude-sonnet-5-5", "utility_effort": "medium"}},
            None,
            ForUtility("Fast"),
            Expected("claude-sonnet-5-5", "medium", "api_key"),
        ),
        (
            {"Balanced": {"utility_model": "claude-opus-5-5", "utility_effort": "high"}},
            None,
            ForUtility("Fast"),
            Expected("claude-opus-5-5", "high", "api_key"),
        ),
        # An effort the model does not have becomes low.
        (
            {"Balanced": {"utility_model": "claude-haiku-5-5", "utility_effort": "extreme"}},
            None,
            ForUtility(),
            Expected("claude-haiku-5-5", "low", "api_key"),
        ),
        # Larger tasks ask for the worker role: the preset's worker model.
        ({}, None, ForUtility("Deep", "worker"), Expected("claude-sonnet-5-5", "medium", "api_key")),
        # Without a worker model anywhere, the worker role falls back to the utility model.
        (
            {"Balanced": {"worker_model": None}, "Deep": {"worker_model": None}},
            None,
            ForUtility("Fast", "worker"),
            Expected("claude-haiku-5-5", "low", "api_key"),
        ),
    ],
)
def test_the_utility_model_falls_back_from_preset_to_setting(
    client, session, preset_changes, utility_setting, request_: ForUtility, expected: Expected
):
    add_key(client, session, "anthropic")
    add_key(client, session, "openai")
    for name, fields in preset_changes.items():
        change_preset(session, name, **fields)
    if utility_setting is not None:
        setting(session, "utility_model", utility_setting)
    check(resolve_provider(session, registry(), ENVIRONMENT, request_), expected)


@dataclass(frozen=True)
class Refusal:
    error: type[ModelChoiceError]
    words: str


@pytest.mark.parametrize(
    ("request_", "providers", "keys", "refusal"),
    [
        # A missing key, in the words of each caller.
        (
            ForReply("anthropic", "claude-opus-5-5", "medium"),
            registry(),
            (),
            Refusal(MissingKey, "No Anthropic API key in this environment. Add one in Settings."),
        ),
        (
            ForSubRun(**CONVERSATION),
            registry(),
            (),
            Refusal(MissingKey, "No Anthropic API key in this environment for Claude Sonnet"),
        ),
        (ForUtility(), registry(), (), Refusal(MissingKey, "No Anthropic API key")),
        (ForSubRun(**CONVERSATION, worker="nobody"), registry(), (), Refusal(UnknownWorker, "No worker named 'nobody'")),
        (
            ForSubRun(**CONVERSATION, worker="codex"),
            registry(codex=False),
            (),
            Refusal(ConnectionUnavailable, "Codex is not available as a worker in this installation."),
        ),
        # A reply may use a connection without tools; a sub-run may not.
        (
            ForSubRun(**CONVERSATION),
            registry(api_keys_run_agents=False),
            ("anthropic",),
            Refusal(CannotRunAgents, "cannot run agents in this environment"),
        ),
        (
            ForReply("nobody", "claude-opus-5-5", "medium"),
            registry(),
            ("anthropic",),
            Refusal(ConnectionUnavailable, "The provider of this conversation is not available."),
        ),
    ],
)
def test_a_request_that_cannot_be_served_is_refused_with_its_reason(
    client, session, request_: ModelRequest, providers: ProviderRegistry, keys: tuple[str, ...], refusal: Refusal
):
    for provider in keys:
        add_key(client, session, provider)
    with pytest.raises(refusal.error) as raised:
        resolve_provider(session, providers, ENVIRONMENT, request_)
    assert refusal.words in str(raised.value)


@pytest.mark.parametrize(
    "request_",
    [ForSubRun(**CONVERSATION, worker="Fast"), ForUtility("Fast")],
)
def test_a_model_missing_from_the_catalog_is_refused_for_sub_runs_and_utility(client, session, request_: ModelRequest):
    add_key(client, session, "anthropic")
    change_preset(session, "Fast", model="no-such-model", utility_model="no-such-model", utility_effort="low")
    with pytest.raises(UnknownModel, match="The model no-such-model is not known."):
        resolve_provider(session, registry(), ENVIRONMENT, request_)


def test_a_reply_does_not_need_its_model_in_the_catalog(client, session):
    add_key(client, session, "anthropic")
    choice = resolve_provider(session, registry(), ENVIRONMENT, ForReply("anthropic", "claude-retired-1", "medium"))
    assert choice.model == "claude-retired-1" and choice.info is None


def test_a_cli_worker_with_a_model_of_another_vendor_is_refused(client, session):
    setting(session, "claude_code_worker", {"model": "gpt-6.1-sol", "effort": "medium"})
    with pytest.raises(WrongVendor, match="Claude Code cannot run the model gpt-6.1-sol"):
        resolve_provider(session, registry(), ENVIRONMENT, ForSubRun(**CONVERSATION, worker="claude_code"))


@pytest.mark.parametrize(
    ("request_", "check_budget", "refused"),
    [
        (ForReply("anthropic", "claude-opus-5-5", "medium"), True, True),
        (ForSubRun(**CONVERSATION), True, True),
        (ForUtility(), True, True),
        # A listing makes no request, and a subscription is not metered.
        (ForSubRun(**CONVERSATION), False, False),
        (ForSubRun(**CONVERSATION, worker="claude_code"), True, False),
    ],
)
def test_a_used_up_budget_refuses_metered_requests(client, session, request_: ModelRequest, check_budget, refused):
    add_key(client, session, "anthropic")
    store_setting(session, SETTING_DEFINITIONS["budget_environment"], ENVIRONMENT, "1")
    session.add(
        UsageRecord(
            environment_id=ENVIRONMENT,
            provider="anthropic",
            model="claude-opus-5-5",
            purpose="main",
            input_tokens=1,
            output_tokens=1,
            cache_read_tokens=0,
            cache_write_tokens=0,
            cost=5.0,
            billing="metered",
            created_at=datetime.now(UTC),
        )
    )
    session.commit()
    if refused:
        with pytest.raises(BudgetReached, match="used up"):
            resolve_provider(session, registry(), ENVIRONMENT, request_, check_budget)
    else:
        resolve_provider(session, registry(), ENVIRONMENT, request_, check_budget)


@pytest.mark.parametrize(
    ("anthropic_connection", "stored", "outcome"),
    [
        ("api_key", "sk-ant-api-real-key", "sk-ant-api-real-key"),
        # A subscription token stored as an API key is refused before use.
        ("api_key", "sk-ant-oat-subscription", UnusableKey),
        ("claude_code", "sk-ant-api-real-key", None),
    ],
)
def test_the_key_is_read_only_when_the_request_is_made(client, session, anthropic_connection, stored, outcome):
    add_key(client, session, "anthropic", stored)
    setting(session, "anthropic_connection", anthropic_connection)
    choice = resolve_provider(session, registry(), ENVIRONMENT, ForReply("anthropic", "claude-opus-5-5", "medium"))
    types = {"anthropic_api_key": ANTHROPIC_API_KEY}
    if isinstance(outcome, type):
        with pytest.raises(outcome, match="subscription token"):
            read_key(session, client.app.state.cipher, types, choice)
    else:
        assert read_key(session, client.app.state.cipher, types, choice) == outcome
