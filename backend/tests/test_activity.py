"""The message queue, waiting approvals across conversations, and the context breakdown."""

from app.providers.base import TokenUsage
from app.providers.fake_provider import ScriptedResponse
from tests.test_chat import add_key, send, start_conversation


def test_the_queue_keeps_messages_in_order_and_hands_them_out(client):
    conversation_id = start_conversation(client)
    queue = f"/api/v1/conversations/{conversation_id}/queue"
    first = client.post(queue, json={"text": "First"}).json()
    second = client.post(queue, json={"text": "Second"}).json()
    client.post(queue, json={"text": "Third"})
    assert client.post(queue, json={"text": "  "}).status_code == 422
    assert client.post(queue, json={"text": "x", "attachment_ids": [999]}).status_code == 422
    assert [item["text"] for item in client.get(queue).json()] == ["First", "Second", "Third"]

    assert client.post(f"{queue}/take", json={}).json() == {"text": "First", "attachment_ids": []}
    assert client.delete(f"{queue}/{first['id']}").status_code == 404
    assert client.post(f"{queue}/take", json={"item_id": second["id"]}).json()["text"] == "Second"
    client.post(queue, json={"text": "Fourth"})
    assert client.post(f"{queue}/take", json={"all": True}).json()["text"] == "Third\n\nFourth"
    assert client.get(queue).json() == []
    assert client.post(f"{queue}/take", json={}).status_code == 404


def test_no_approvals_wait_without_runs(client):
    assert client.get("/api/v1/environments/1/approvals").json() == []


def test_the_context_breakdown_lists_parts_and_the_last_reply(client, fake):
    add_key(client)
    conversation_id = start_conversation(client)
    fake.responses.append(
        ScriptedResponse(text="Hello there", usage=TokenUsage(input_tokens=100, output_tokens=20, cache_read_tokens=300))
    )
    send(client, conversation_id, "Hi")

    breakdown = client.get(f"/api/v1/conversations/{conversation_id}/context").json()
    labels = [part["label"] for part in breakdown["parts"]]
    assert labels[0] == "Base prompt" and labels[-1] == "Conversation so far"
    assert all(part["tokens"] > 0 for part in breakdown["parts"])
    assert breakdown["last_reply"]["cache_share"] == 0.75
    assert breakdown["context_tokens"] == 420 and breakdown["context_window"] == 1_000_000
