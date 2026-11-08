from app.providers.base import TokenUsage
from app.providers.fake_provider import ScriptedResponse
from tests.test_chat import add_key, send, start_conversation

EXPENSIVE = TokenUsage(input_tokens=400_000, output_tokens=20_000)


def test_a_used_up_budget_pauses_metered_replies_only(client, fake):
    add_key(client)
    conversation_id = start_conversation(client)
    fake.responses.append(ScriptedResponse(text="Done", usage=EXPENSIVE))
    send(client, conversation_id, "Big job")
    spent = client.get("/api/v1/environments/1/usage").json()["cost"]
    assert spent > 1

    assert client.put("/api/v1/environments/1/settings/budget_environment", json={"value": "1"}).status_code == 200
    summary = client.get("/api/v1/environments/1/usage").json()
    assert summary["budgets"] == [
        {"scope": "environment", "label": "Private budget", "limit": 1.0, "spent": round(spent, 6)}
    ]
    refused = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "More"})
    assert refused.status_code == 409 and "budget" in refused.json()["detail"]

    # The subscription costs nothing per reply, so the budget does not stop it.
    client.put("/api/v1/settings/anthropic_connection", json={"value": "claude_code"})
    other = start_conversation(client)
    fake.responses.append(ScriptedResponse(text="Fine"))
    send(client, other, "Still here?")


def test_the_total_budget_counts_every_environment(client, fake):
    add_key(client)
    conversation_id = start_conversation(client)
    fake.responses.append(ScriptedResponse(text="Done", usage=EXPENSIVE))
    send(client, conversation_id, "Big job")

    client.put("/api/v1/settings/budget_total", json={"value": "$0.50"})
    client.put("/api/v1/environments/2/credentials/anthropic_api_key", json={"value": "sk-test-valid-key-1234"})
    elsewhere = start_conversation(client, environment_id=2)
    refused = client.post(f"/api/v1/conversations/{elsewhere}/messages", json={"text": "Hi"})
    assert refused.status_code == 409 and "all environments" in refused.json()["detail"]

    assert client.put("/api/v1/settings/budget_total", json={"value": "lots"}).status_code == 422
    assert client.put("/api/v1/settings/budget_total", json={"value": ""}).status_code == 200
    assert client.put("/api/v1/settings/budget_environment", json={"value": "5"}).status_code == 422
