"""Switching an environment's presets between vendors (presets/vendor.py): the model of the same size per role, the
utility model with them, efforts the new model lacks replaced, and a switch the model policy forbids refused."""

import pytest

from app.presets.vendor import counterpart
from app.providers.catalog import OPENAI_MODELS
from app.providers.fake_provider import FakeProvider
from app.providers.registry import ProviderSpec, default_provider_registry

URL = "/api/v1/environments/1/presets/vendor"
CLAUDE_PRESETS = [
    ("Fast", "claude-sonnet-5-5", "low", None, None),
    ("Balanced", "claude-opus-5-5", "medium", "claude-sonnet-5-5", "low"),
    ("Deep", "claude-opus-5-5", "high", "claude-sonnet-5-5", "medium"),
    ("Cheap", "claude-haiku-5-5", "low", None, None),
]
OPENAI_PRESETS = [
    ("Fast", "gpt-6.1-sol", "low", None, None),
    ("Balanced", "gpt-6-astra", "medium", "gpt-6.1-sol", "low"),
    ("Deep", "gpt-6-astra", "high", "gpt-6.1-sol", "medium"),
    ("Cheap", "gpt-6-luna", "low", None, None),
]


@pytest.fixture(autouse=True)
def openai_offered(client) -> None:
    """The test installation knows only Anthropic; add OpenAI's models with both of its connections."""

    def create(_api_key: str | None) -> FakeProvider:
        return FakeProvider("gpt-6-astra", [], [])

    registry = client.app.state.providers
    registry.add(ProviderSpec("openai", "OpenAI", "openai_api_key", create, connection="api_key"), OPENAI_MODELS)
    registry.add(ProviderSpec("openai", "Codex", None, create, connection="codex", metered=False), [])


def preset_rows(client) -> list[tuple]:
    rows = client.get("/api/v1/environments/1/presets").json()
    return [(row["name"], row["model"], row["effort"], row["worker_model"], row["worker_effort"]) for row in rows]


def utility_model(client) -> str:
    settings = client.get("/api/v1/environments/1/settings").json()
    return next(setting["value"] for setting in settings if setting["key"] == "utility_model")


@pytest.mark.parametrize(
    ("model", "vendor", "expected"),
    [
        ("claude-opus-5-5", "openai", "gpt-6-astra"),
        ("claude-sonnet-5-5", "openai", "gpt-6.1-sol"),
        ("claude-haiku-5-5", "openai", "gpt-6-luna"),
        ("gpt-6-astra", "anthropic", "claude-opus-5-5"),
        ("gpt-6.1-terra", "anthropic", "claude-sonnet-5-5"),
        ("gpt-6-luna", "anthropic", "claude-haiku-5-5"),
        # Already the vendor's own, unknown or empty: nothing to replace.
        ("claude-opus-5-5", "anthropic", None),
        ("some-local-model", "openai", None),
        ("", "openai", None),
        (None, "openai", None),
    ],
)
def test_a_model_moves_to_the_vendors_model_of_the_same_size(model, vendor, expected):
    found = counterpart(default_provider_registry(), model, vendor)
    assert (found.id if found else None) == expected


def test_the_presets_switch_to_openai_and_back(client):
    state = client.get(URL).json()
    assert [vendor["name"] for vendor in state["vendors"]] == ["anthropic", "openai"]
    assert state["current"] == "anthropic"
    assert preset_rows(client) == CLAUDE_PRESETS

    switched = client.put(URL, json={"vendor": "openai"})
    assert switched.status_code == 200
    # Four main models, two workers and the environment's utility model.
    assert switched.json()["changed"] == 7 and switched.json()["current"] == "openai"
    assert preset_rows(client) == OPENAI_PRESETS
    assert utility_model(client) == "gpt-6-luna"

    back = client.put(URL, json={"vendor": "anthropic"}).json()
    assert back["changed"] == 7 and back["current"] == "anthropic"
    assert preset_rows(client) == CLAUDE_PRESETS
    assert utility_model(client) == "claude-haiku-5-5"


def test_presets_of_both_vendors_have_no_current_vendor(client):
    fast = client.get("/api/v1/environments/1/presets").json()[0]
    client.patch(f"/api/v1/presets/{fast['id']}", json={"model": "gpt-6.1-sol", "effort": "low"})
    assert client.get(URL).json()["current"] is None


def test_an_effort_the_new_model_lacks_becomes_its_default(client):
    cheap = client.get("/api/v1/environments/1/presets").json()[3]
    client.patch(f"/api/v1/presets/{cheap['id']}", json={"model": "gpt-6-luna", "effort": "none"})
    client.put(URL, json={"vendor": "anthropic"})
    assert preset_rows(client)[3] == ("Cheap", "claude-haiku-5-5", "medium", None, None)


def test_a_switch_the_model_policy_forbids_changes_nothing(client):
    policy = {"value": {"connections": ["anthropic:api_key", "anthropic:claude_code"]}}
    assert client.put("/api/v1/environments/1/settings/model_policy", json=policy).status_code == 200
    refused = client.put(URL, json={"vendor": "openai"})
    assert refused.status_code == 422 and "model policy" in refused.json()["detail"]
    assert preset_rows(client) == CLAUDE_PRESETS


def test_an_unknown_vendor_is_refused(client):
    assert client.put(URL, json={"vendor": "ollama"}).status_code == 422
