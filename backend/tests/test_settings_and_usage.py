from app.providers.base import TokenUsage
from app.providers.fake_provider import ScriptedResponse
from tests.conftest import VALID_KEY


def setting(settings: list[dict], key: str) -> dict:
    return next(item for item in settings if item["key"] == key)


def test_global_settings_start_at_application_defaults(client):
    model = setting(client.get("/api/v1/settings").json(), "utility_model")
    assert model["value"] == "claude-haiku-5-5"
    assert model["source"] == "default"


def test_environment_inherits_global_until_it_overrides(client):
    client.put("/api/v1/settings/utility_model", json={"value": "claude-sonnet-5-5"})

    inherited = setting(client.get("/api/v1/environments/2/settings").json(), "utility_model")
    assert inherited["value"] == "claude-sonnet-5-5"
    assert inherited["source"] == "global"

    overridden = client.put("/api/v1/environments/2/settings/utility_model", json={"value": "claude-haiku-5-5"}).json()
    assert overridden["value"] == "claude-haiku-5-5"
    assert overridden["source"] == "environment"
    assert overridden["inherited_value"] == "claude-sonnet-5-5"
    assert overridden["inherited_source"] == "global"

    # The other environment still inherits the global value.
    other = setting(client.get("/api/v1/environments/1/settings").json(), "utility_model")
    assert other["value"] == "claude-sonnet-5-5"

    reset = client.delete("/api/v1/environments/2/settings/utility_model").json()
    assert reset["value"] == "claude-sonnet-5-5"
    assert reset["source"] == "global"


def test_global_only_settings_cannot_be_set_per_environment(client):
    response = client.put("/api/v1/environments/1/settings/display_name", json={"value": "Alex"})
    assert response.status_code == 422
    assert "global" in response.json()["detail"]
    assert all(item["key"] != "display_name" for item in client.get("/api/v1/environments/1/settings").json())


def test_setting_values_are_validated(client):
    assert client.put("/api/v1/settings/utility_model", json={"value": "gpt-x"}).status_code == 422
    assert client.put("/api/v1/settings/display_name", json={"value": "x" * 61}).status_code == 422
    assert client.put("/api/v1/settings/display_name", json={"value": "  Alex "}).json()["value"] == "Alex"


def test_usage_and_context_are_reported(client, fake):
    client.put("/api/v1/environments/1/credentials/anthropic_api_key", json={"value": VALID_KEY})
    conversation_id = client.post(
        "/api/v1/environments/1/conversations", json={"model": "claude-opus-5-5", "effort": "medium"}
    ).json()["id"]
    fake.responses.append(
        ScriptedResponse(text="Hoi", usage=TokenUsage(input_tokens=1_000, output_tokens=500, cache_read_tokens=2_000))
    )
    client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Hallo"})

    conversation = client.get(f"/api/v1/conversations/{conversation_id}").json()
    assert conversation["context_tokens"] == 3_500
    assert conversation["context_window"] == 1_000_000

    usage = client.get("/api/v1/environments/1/usage").json()
    assert usage["priced_records"] == 1
    assert usage["cost"] == round((1_000 * 4.00 + 500 * 20.00 + 2_000 * 0.20) / 1_000_000, 6)
    assert client.get("/api/v1/environments/2/usage").json()["priced_records"] == 0
