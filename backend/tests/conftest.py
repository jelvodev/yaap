import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from types import ModuleType

import pytest
from argon2 import PasswordHasher
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.blueprint import layers as blueprint_layers
from app.credentials.types import CredentialTestResult, CredentialType
from app.main import create_app
from app.providers.base import AgentRequest
from app.providers.catalog import ANTHROPIC_MODELS
from app.providers.fake_provider import FakeProvider, ScriptedResponse
from app.providers.registry import ProviderRegistry, ProviderSpec

VALID_KEY = "sk-test-valid-key-1234"
TEST_PASSWORD = "test-password-123"
# Argon2id with the smallest parameters: signing in for every test would otherwise cost seconds.
CHEAP_HASHER = PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1)


def pass_to_cli(monkeypatch: pytest.MonkeyPatch, provider_module: ModuleType, *names: str) -> None:
    """A provider CLI gets only the variables it needs from YAAP's environment; a fake CLI also needs its own."""
    monkeypatch.setattr(provider_module, "INHERITED_ENVIRONMENT", provider_module.INHERITED_ENVIRONMENT | set(names))


def sign_in(test_client: TestClient) -> None:
    """Set the test password (or sign in with it) and send the session's CSRF token with every request, as the
    frontend does."""
    state = test_client.get("/api/v1/auth/state").json()
    path = "/api/v1/auth/sign-in" if state["password_set"] else "/api/v1/auth/setup"
    response = test_client.post(path, json={"password": TEST_PASSWORD})
    assert response.status_code == 200, response.text
    test_client.headers["X-CSRF-Token"] = test_client.cookies["yaap_csrf"]


@dataclass
class FakeBackend:
    responses: list[ScriptedResponse] = field(default_factory=list)
    requests: list[AgentRequest] = field(default_factory=list)
    keys_used: list[str] = field(default_factory=list)


async def fake_key_test(value: str) -> CredentialTestResult:
    if value == VALID_KEY:
        return CredentialTestResult(True, "The key works.")
    return CredentialTestResult(False, "Anthropic rejected this key.")


@pytest.fixture(autouse=True)
def no_blueprint_cache() -> Iterator[None]:
    """Tests write Blueprint files straight to disk and expect them at once; the merge cache has its own test."""
    blueprint_layers.CACHE_SECONDS = 0
    blueprint_layers.forget_merged()
    yield
    blueprint_layers.forget_merged()


@pytest.fixture(autouse=True)
def test_client_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """TestClient sends `Host: testserver`; the browser boundary answers only on allowed hosts."""
    monkeypatch.setenv("YAAP_ALLOWED_HOSTS", "testserver")


@pytest.fixture(autouse=True)
def commands_in_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Commands, MCP servers and the browser run in the test process: the suite needs no Docker. The runner's own
    tests (test_runner.py) switch it on against the helper on a fake Docker API."""
    monkeypatch.setenv("YAAP_RUNNER", "off")


@pytest.fixture
def fake() -> FakeBackend:
    return FakeBackend()


def make_test_app(fake: FakeBackend) -> FastAPI:
    """The app the client fixture runs, for tests that start YAAP more than once on one data folder (plugins)."""

    def create_provider(api_key: str) -> FakeProvider:
        fake.keys_used.append(api_key)
        return FakeProvider("claude-opus-5-5", fake.responses, fake.requests)

    credential_types = {
        "anthropic_api_key": CredentialType(
            kind="anthropic_api_key", label="Anthropic API key", description="Test key.", test=fake_key_test
        )
    }
    def create_subscription_provider(_api_key: str | None) -> FakeProvider:
        fake.keys_used.append("<claude code>")
        return FakeProvider("claude-opus-5-5", fake.responses, fake.requests, billing="subscription")

    providers = ProviderRegistry(
        [
            ProviderSpec("anthropic", "Anthropic", "anthropic_api_key", create_provider, connection="api_key", runs_agents=True),
            ProviderSpec(
                "anthropic",
                "Claude Code",
                None,
                create_subscription_provider,
                connection="claude_code",
                runs_agents=True,
                metered=False,
            ),
        ],
        ANTHROPIC_MODELS,
    )
    app = create_app(credential_types=credential_types, providers=providers)
    app.state.password_hasher = CHEAP_HASHER
    # Titles from the utility model would use up scripted responses; tests that need them switch this on.
    app.state.utility_titles = False
    app.state.memory_suggestions = False
    app.state.memory_tidy = False
    app.state.reviews_after_goals = False
    app.state.scheduler = False
    # No update check at start: it would call GitHub.
    app.state.update_check_at_start = False
    # No search service; web search tests set a fake one.
    app.state.web_search = None
    # No embedding model and no background indexing; search tests set an embedder and run passes themselves.
    app.state.load_embedder = lambda: None
    app.state.background_indexing = False
    return app


@pytest.fixture
def master_key(monkeypatch: pytest.MonkeyPatch) -> str:
    """The master key the client fixture starts YAAP with. YAAP takes it out of the environment once read, so a test
    that needs it again asks for this fixture."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("YAAP_MASTER_KEY", key)
    return key


@pytest.fixture
def client(tmp_path, monkeypatch, fake: FakeBackend, master_key: str) -> Iterator[TestClient]:
    monkeypatch.setenv("YAAP_DATA_DIR", str(tmp_path))
    app = make_test_app(fake)
    with TestClient(app) as test_client:
        # Code that must run in a worker thread of the app's event loop (the task loop) runs through this portal.
        app.state.portal = test_client.portal
        sign_in(test_client)
        # Most tests exercise the API-key connection; tests for Claude Code switch it back.
        test_client.put("/api/v1/settings/anthropic_connection", json={"value": "api_key"})
        yield test_client


def parse_events(body: str) -> list[tuple[str, dict]]:
    events = []
    for chunk in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in chunk.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events
