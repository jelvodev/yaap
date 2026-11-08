from app.providers.base import TokenUsage
from app.providers.fake_provider import ScriptedResponse
from tests.conftest import VALID_KEY, parse_events


def start_conversation(client, environment_id: int = 1) -> int:
    response = client.post(
        f"/api/v1/environments/{environment_id}/conversations",
        json={"model": "claude-opus-5-5", "effort": "medium"},
    )
    assert response.status_code == 201
    return response.json()["id"]


def add_key(client, environment_id: int = 1) -> None:
    client.put(f"/api/v1/environments/{environment_id}/credentials/anthropic_api_key", json={"value": VALID_KEY})


def send(client, conversation_id: int, text: str) -> list[tuple[str, dict]]:
    response = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": text})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    return parse_events(response.text)


def test_chat_requires_a_key(client):
    conversation_id = start_conversation(client)
    response = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Hallo"})
    assert response.status_code == 409
    assert "Add one in Settings" in response.json()["detail"]


def test_unknown_model_or_effort_is_rejected(client):
    unknown_model = client.post("/api/v1/environments/1/conversations", json={"model": "gpt-x", "effort": "low"})
    assert unknown_model.status_code == 422
    bad_effort = client.post(
        "/api/v1/environments/1/conversations", json={"model": "claude-opus-5-5", "effort": "extreme"}
    )
    assert bad_effort.status_code == 422


def test_streamed_reply_is_stored_with_usage(client, fake):
    add_key(client)
    conversation_id = start_conversation(client)
    fake.responses.append(
        ScriptedResponse(
            text="Hallo, hoe kan ik helpen?",
            reasoning="The user greets in Dutch.",
            usage=TokenUsage(input_tokens=1000, output_tokens=200),
        )
    )

    events = send(client, conversation_id, "Hallo YAAP")

    names = [name for name, _ in events]
    assert names[0] == "user_message"
    assert "reasoning_delta" in names and "text_delta" in names
    assert names[-1] == "done"
    done = events[-1][1]
    assert done["message"]["status"] == "complete"
    assert [(block["type"], block["text"]) for block in done["message"]["content"]] == [
        ("reasoning", "The user greets in Dutch."),
        ("text", "Hallo, hoe kan ik helpen? "),
    ]
    assert done["conversation"]["title"] == "Hallo YAAP"
    assert fake.keys_used == [VALID_KEY]

    conversation = client.get(f"/api/v1/conversations/{conversation_id}").json()
    assert [message["role"] for message in conversation["messages"]] == ["user", "assistant"]


def test_history_replays_raw_assistant_turns(client, fake):
    add_key(client)
    conversation_id = start_conversation(client)
    fake.responses.extend([ScriptedResponse(text="First answer", reasoning="Thinking"), ScriptedResponse(text="Second")])

    send(client, conversation_id, "One")
    send(client, conversation_id, "Two")

    second_request = fake.requests[1]
    assert [message.role for message in second_request.messages] == ["user", "assistant", "user"]
    assert second_request.messages[1].provider_raw == [
        {"type": "thinking", "thinking": "Thinking", "signature": "fake-signature"},
        {"type": "text", "text": "First answer"},
    ]
    assert second_request.effort == "medium"
    assert "Reply in the language of the user's latest message" in second_request.system[0].text


def test_refused_and_failed_replies_stay_out_of_history(client, fake):
    add_key(client)
    conversation_id = start_conversation(client)
    fake.responses.extend(
        [
            ScriptedResponse(text="", stop_reason="refusal", refusal_category="cyber"),
            ScriptedResponse(text="partial", error="Anthropic rate limit reached. Try again in a moment."),
            ScriptedResponse(text="Fine"),
        ]
    )

    refused = send(client, conversation_id, "First")[-1][1]["message"]
    assert refused["status"] == "refused"
    assert "cyber" in refused["error"]

    failed = send(client, conversation_id, "Second")[-1][1]["message"]
    assert failed["status"] == "error"
    assert failed["error"] == "Anthropic rate limit reached. Try again in a moment."

    send(client, conversation_id, "Third")
    assert [message.role for message in fake.requests[2].messages] == ["user", "user", "user"]


def test_conversations_are_listed_per_environment(client, fake):
    start_conversation(client, environment_id=1)
    assert len(client.get("/api/v1/environments/1/conversations").json()) == 1
    assert client.get("/api/v1/environments/2/conversations").json() == []


def test_stored_subscription_token_is_refused_before_calling_the_provider(client, fake):
    # A token saved before the format check existed must not reach the provider.
    from dataclasses import replace

    from app.credentials.store import save_credential
    from app.credentials.types import CredentialTestResult, find_anthropic_key_problem

    state = client.app.state
    state.credential_types["anthropic_api_key"] = replace(
        state.credential_types["anthropic_api_key"], find_format_problem=find_anthropic_key_problem
    )
    with state.session_factory() as session:
        save_credential(
            session, state.cipher, 1, "anthropic_api_key", "sk-ant-oat01-old-token", CredentialTestResult(True, "ok")
        )
    conversation_id = start_conversation(client)

    response = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Hallo"})

    assert response.status_code == 409
    assert "subscription token" in response.json()["detail"]
    assert "Replace the stored key" in response.json()["detail"]
    assert fake.keys_used == []


def test_times_from_the_database_are_marked_utc(client, fake):
    """SQLite keeps no zone; without the mark the browser reads UTC as local time (the Agents page was an hour off)."""
    add_key(client)
    conversation_id = start_conversation(client)
    fake.responses.append(ScriptedResponse(text="Hoi"))
    send(client, conversation_id, "Hallo")

    conversation = client.get(f"/api/v1/conversations/{conversation_id}").json()
    times = [message["created_at"] for message in conversation["messages"]]
    listed = client.get("/api/v1/environments/1/conversations").json()
    times += [item["updated_at"] for item in listed]
    assert times and all(value.endswith(("Z", "+00:00")) for value in times), times
