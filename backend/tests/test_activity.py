"""The message queue, waiting approvals across conversations, and the context breakdown."""

import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.core.chat_history import add_user_message
from app.core.reply_errors import ReplyUnavailable
from app.core.reply_service import ReplyService
from app.core.message_queue import (
    QueueSteeringHandoff,
    add_steering,
    request_send_now,
    restore_interrupted_handoffs,
)
from app.providers.base import TokenUsage
from app.providers.fake_provider import ScriptedResponse
from app.storage.models import Conversation
from tests.conftest import FakeBackend
from tests.test_chat import add_key, send, start_conversation


def test_the_queue_keeps_messages_in_order_and_legacy_take_does_not_consume_them(client):
    conversation_id = start_conversation(client)
    queue = f"/api/v1/conversations/{conversation_id}/queue"
    first = client.post(queue, json={"text": "First"}).json()
    second = client.post(queue, json={"text": "Second"}).json()
    client.post(queue, json={"text": "Third"})
    assert client.post(queue, json={"text": "  "}).status_code == 422
    assert client.post(queue, json={"text": "x", "attachment_ids": [999]}).status_code == 422
    assert [item["text"] for item in client.get(queue).json()] == ["First", "Second", "Third"]

    refused = client.post(f"{queue}/take", json={})
    assert refused.status_code == 409
    assert [item["text"] for item in client.get(queue).json()] == ["First", "Second", "Third"]
    assert client.delete(f"{queue}/{first['id']}").status_code == 204
    assert client.post(f"{queue}/take", json={"item_id": second["id"]}).status_code == 409
    client.post(queue, json={"text": "Fourth"})
    assert client.post(f"{queue}/take", json={"all": True}).status_code == 409
    assert [item["text"] for item in client.get(queue).json()] == ["Second", "Third", "Fourth"]


def test_sending_a_queue_selection_consumes_it_with_its_message(client, fake):
    add_key(client)
    conversation_id = start_conversation(client)
    queue = f"/api/v1/conversations/{conversation_id}/queue"
    first = client.post(queue, json={"text": "First queued"}).json()
    client.post(queue, json={"text": "Keep this one"})
    fake.responses.append(ScriptedResponse(text="Accepted."))

    response = client.post(f"{queue}/send", json={"item_id": first["id"]})
    assert response.status_code == 200, response.text
    assert [item["text"] for item in client.get(queue).json()] == ["Keep this one"]
    conversation = client.get(f"/api/v1/conversations/{conversation_id}").json()
    assert any(
        block.get("type") == "text" and block.get("text") == "First queued"
        for message in conversation["messages"]
        for block in message["content"]
    )


def test_retry_reuses_the_saved_user_message(client, fake):
    add_key(client)
    conversation_id = start_conversation(client)
    with client.app.state.session_factory() as session:
        conversation = session.get(Conversation, conversation_id)
        assert conversation is not None
        message = add_user_message(session, conversation, "Already saved")
        message_id = message.id
    fake.responses.append(ScriptedResponse(text="Retried reply"))

    response = client.post(f"/api/v1/conversations/{conversation_id}/messages/{message_id}/retry")

    assert response.status_code == 200, response.text
    stored = client.get(f"/api/v1/conversations/{conversation_id}").json()["messages"]
    assert sum(message["role"] == "user" for message in stored) == 1
    assert any(message["role"] == "assistant" and message["status"] == "complete" for message in stored)


def test_queue_reply_start_failure_can_retry_the_committed_message(
    client: TestClient, fake: FakeBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    add_key(client)
    conversation_id = start_conversation(client)
    queue = f"/api/v1/conversations/{conversation_id}/queue"
    item = client.post(queue, json={"text": "Keep this message"}).json()

    def fail_launch(*_args: object, **_kwargs: object) -> None:
        raise ReplyUnavailable("The reply could not start.")

    with monkeypatch.context() as patch:
        patch.setattr(ReplyService, "launch", fail_launch)
        response = client.post(f"{queue}/send", json={"item_id": item["id"]})
    assert response.status_code == 409
    assert client.get(queue).json() == []
    messages = client.get(f"/api/v1/conversations/{conversation_id}").json()["messages"]
    assert [message["role"] for message in messages] == ["user"]

    fake.responses.append(ScriptedResponse(text="Recovered"))
    retried = client.post(f"/api/v1/conversations/{conversation_id}/messages/{messages[0]['id']}/retry")
    assert retried.status_code == 200
    messages = client.get(f"/api/v1/conversations/{conversation_id}").json()["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant"]


def test_queue_send_refusal_keeps_selected_rows_available(client, fake):
    conversation_id = start_conversation(client)
    queue = f"/api/v1/conversations/{conversation_id}/queue"
    item = client.post(queue, json={"text": "Keep me"}).json()

    response = client.post(f"{queue}/send", json={"item_id": item["id"]})
    assert response.status_code == 409
    assert [queued["id"] for queued in client.get(queue).json()] == [item["id"]]


@pytest.mark.parametrize("command", ["/compact focus", "/eval"])
def test_queued_standalone_command_is_refused_without_consuming_the_selection(client, fake, command):
    add_key(client)
    conversation_id = start_conversation(client)
    queue = f"/api/v1/conversations/{conversation_id}/queue"
    item = client.post(queue, json={"text": command}).json()

    response = client.post(f"{queue}/send", json={"item_id": item["id"]})

    assert response.status_code == 409
    assert [queued["id"] for queued in client.get(queue).json()] == [item["id"]]


def test_queue_send_rejects_oversized_combined_text_without_consuming_rows(client, fake):
    add_key(client)
    conversation_id = start_conversation(client)
    queue = f"/api/v1/conversations/{conversation_id}/queue"
    first = client.post(queue, json={"text": "a" * 150_000}).json()
    second = client.post(queue, json={"text": "b" * 150_000}).json()

    response = client.post(f"{queue}/send", json={"all": True})
    assert response.status_code == 422
    assert [item["id"] for item in client.get(queue).json()] == [first["id"], second["id"]]


def test_send_now_continues_server_side_after_the_original_stream_disconnects(client, fake):
    add_key(client)
    conversation_id = start_conversation(client)
    fake.responses.extend([ScriptedResponse(text="busy reply with several words", delay=0.1), ScriptedResponse(text="Queued reply")])
    queue = f"/api/v1/conversations/{conversation_id}/queue"
    item = client.post(queue, json={"text": "Send me now"}).json()

    with ThreadPoolExecutor(max_workers=1) as workers:
        original = workers.submit(
            client.post,
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"text": "Start the first reply"},
        )
        for _ in range(100):
            if conversation_id in client.get("/api/v1/environments/1/replies").json():
                break
            time.sleep(0.02)
        response = client.post(f"{queue}/send-now", json={"item_id": item["id"]})
        assert response.status_code == 202, response.text
        assert original.result(timeout=10).status_code == 200

    for _ in range(100):
        conversation = client.get(f"/api/v1/conversations/{conversation_id}").json()
        if any(
            block.get("type") == "text" and block.get("text") == "Send me now"
            for message in conversation["messages"]
            for block in message["content"]
        ):
            break
        time.sleep(0.05)
    else:
        raise AssertionError("The server did not send the saved queue selection after the reply stopped.")
    assert client.get(queue).json() == []


def test_steering_is_acknowledged_at_handoff_or_restored_when_run_ends(client):
    conversation_id = start_conversation(client)
    queue = f"/api/v1/conversations/{conversation_id}/queue"
    item = client.post(queue, json={"text": "Steer this"}).json()
    store = QueueSteeringHandoff(client.app.state.session_factory)
    with client.app.state.session_factory() as session:
        add_steering(session, conversation_id, 42, "", item["id"])

    assert store.claim(conversation_id, 42) == [(item["id"], "Steer this")]
    assert store.claim(conversation_id, 42) == []
    store.restore(42)
    assert client.get(queue).json()[0]["steering_pending"] is False

    with client.app.state.session_factory() as session:
        add_steering(session, conversation_id, 43, "", item["id"])
    assert store.claim(conversation_id, 43) == [(item["id"], "Steer this")]
    store.acknowledge(43, [item["id"]])
    assert client.get(queue).json() == []


def test_send_now_selection_survives_until_restart_restores_it(client):
    conversation_id = start_conversation(client)
    queue = f"/api/v1/conversations/{conversation_id}/queue"
    selected = client.post(queue, json={"text": "Send after stop"}).json()
    client.post(queue, json={"text": "Keep waiting"})

    with client.app.state.session_factory() as session:
        ids = request_send_now(session, conversation_id, selected["id"], False, "target-job")
    assert ids == [selected["id"]]
    pending = client.get(queue).json()
    assert pending[0]["send_now_pending"] is True
    assert pending[1]["send_now_pending"] is False

    restore_interrupted_handoffs(client.app.state.session_factory)
    restored = client.get(queue).json()
    assert [item["id"] for item in restored] == [selected["id"], pending[1]["id"]]
    assert all(not item["send_now_pending"] for item in restored)


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
