from app.providers.fake_provider import ScriptedResponse
from tests.conftest import parse_events
from tests.test_chat import add_key, send, start_conversation


def conversation(client, conversation_id: int) -> dict:
    return client.get(f"/api/v1/conversations/{conversation_id}").json()


def texts(data: dict) -> list[str]:
    return ["".join(block["text"] for block in message["content"] if block["type"] == "text").strip() for message in data["messages"]]


def two_turns(client, fake) -> int:
    add_key(client)
    conversation_id = start_conversation(client)
    fake.responses += [ScriptedResponse(text="Paris"), ScriptedResponse(text="About 2 million")]
    send(client, conversation_id, "Capital of France?")
    send(client, conversation_id, "How many people live there?")
    return conversation_id


def test_editing_a_message_makes_a_new_version_and_continues_from_it(client, fake):
    conversation_id = two_turns(client, fake)
    first_question = conversation(client, conversation_id)["messages"][0]

    fake.responses.append(ScriptedResponse(text="Berlin"))
    response = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"text": "Capital of Germany?", "edit_of": first_question["id"]},
    )
    assert response.status_code == 200, response.text
    assert parse_events(response.text)[-1][0] == "done"

    data = conversation(client, conversation_id)
    assert texts(data) == ["Capital of Germany?", "Berlin"]
    assert data["messages"][0]["version_ids"] == [first_question["id"], data["messages"][0]["id"]]
    # The model only saw the new branch.
    assert [message.role for message in fake.requests[-1].messages] == ["user"]


def test_a_reply_can_be_made_again_and_versions_can_be_switched(client, fake):
    conversation_id = two_turns(client, fake)
    second_reply = conversation(client, conversation_id)["messages"][3]

    fake.responses.append(ScriptedResponse(text="Roughly 2.1 million"))
    regenerated = client.post(f"/api/v1/conversations/{conversation_id}/messages/{second_reply['id']}/regenerate")
    assert regenerated.status_code == 200, regenerated.text

    data = conversation(client, conversation_id)
    assert texts(data)[-1] == "Roughly 2.1 million"
    assert data["messages"][3]["version_ids"] == [second_reply["id"], data["messages"][3]["id"]]
    assert len(fake.requests[-1].messages) == 3  # question, answer, question

    switched = client.put(f"/api/v1/conversations/{conversation_id}/head", json={"message_id": second_reply["id"]})
    assert switched.status_code == 200
    assert texts(switched.json())[-1] == "About 2 million"

    # Switching to an earlier message continues to its newest follow-up.
    first_question = data["messages"][0]["id"]
    back = client.put(f"/api/v1/conversations/{conversation_id}/head", json={"message_id": first_question}).json()
    assert texts(back)[-1] == "Roughly 2.1 million"


def test_only_own_messages_and_replies_of_the_conversation_qualify(client, fake):
    conversation_id = two_turns(client, fake)
    messages = conversation(client, conversation_id)["messages"]
    assert client.post(
        f"/api/v1/conversations/{conversation_id}/messages", json={"text": "x", "edit_of": messages[1]["id"]}
    ).status_code == 422
    assert client.post(f"/api/v1/conversations/{conversation_id}/messages/{messages[0]['id']}/regenerate").status_code == 422
    other = start_conversation(client)
    assert client.put(f"/api/v1/conversations/{other}/head", json={"message_id": messages[0]["id"]}).status_code == 422
