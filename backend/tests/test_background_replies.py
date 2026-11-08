import time

from app.providers.fake_provider import ScriptedResponse
from tests.conftest import parse_events
from tests.test_chat import add_key, start_conversation


def wait_until_idle(client, environment_id: int = 1, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while client.get(f"/api/v1/environments/{environment_id}/replies").json():
        assert time.monotonic() < deadline, "the reply did not finish"
        time.sleep(0.05)


def assistant_messages(client, conversation_id: int) -> list[dict]:
    messages = client.get(f"/api/v1/conversations/{conversation_id}").json()["messages"]
    return [message for message in messages if message["role"] == "assistant"]


def test_a_reply_keeps_going_when_the_page_leaves_and_can_be_followed_again(client, fake):
    add_key(client)
    conversation_id = start_conversation(client)
    fake.responses.append(ScriptedResponse(text="one two three four five six", delay=0.15))

    # The page reads the first event and goes away, as when the user opens Settings.
    with client.stream("POST", f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Count"}) as response:
        first = next(response.iter_lines())
        assert first == "event: user_message"
    assert client.get("/api/v1/environments/1/replies").json() == [conversation_id]

    # Coming back: the reply is followed from its first event.
    followed = client.get(f"/api/v1/conversations/{conversation_id}/reply")
    assert followed.status_code == 200
    events = parse_events(followed.text)
    assert events[0][0] == "user_message" and events[-1][0] == "done"
    assert "".join(data["text"] for name, data in events if name == "text_delta") == "one two three four five six "

    wait_until_idle(client)
    [reply] = assistant_messages(client, conversation_id)
    assert reply["status"] == "complete"
    assert client.get(f"/api/v1/conversations/{conversation_id}/reply").status_code == 204


def test_a_second_message_waits_for_the_running_reply(client, fake):
    add_key(client)
    conversation_id = start_conversation(client)
    fake.responses.append(ScriptedResponse(text="a b c d e f g h", delay=0.1))
    with client.stream("POST", f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Go"}) as response:
        next(response.iter_lines())

    busy = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "And?"})
    assert busy.status_code == 409 and "still replying" in busy.json()["detail"]
    wait_until_idle(client)


def test_stopping_keeps_what_arrived_as_incomplete(client, fake):
    add_key(client)
    conversation_id = start_conversation(client)
    fake.responses.append(ScriptedResponse(text=" ".join(["word"] * 40), delay=0.1))
    with client.stream("POST", f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Talk"}) as response:
        lines = response.iter_lines()
        while next(lines) != "event: text_delta":
            pass

    assert client.post(f"/api/v1/conversations/{conversation_id}/reply/stop").status_code == 204
    assert client.get("/api/v1/environments/1/replies").json() == []
    [reply] = assistant_messages(client, conversation_id)
    assert reply["status"] == "incomplete"
    assert reply["content"][0]["text"].startswith("word")
    # Stopping when nothing runs is fine.
    assert client.post(f"/api/v1/conversations/{conversation_id}/reply/stop").status_code == 204
