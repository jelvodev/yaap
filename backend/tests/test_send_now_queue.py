"""Send-now of queued messages and commands while a reply runs: the handoff ends with the queue settled, never with
a row left waiting for a job that is gone."""

from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from app.providers.fake_provider import ScriptedResponse
from tests.conftest import FakeBackend
from tests.test_context_control import summaries
from tests.test_queue_commands import (
    conversation_with_turns,
    queue_url,
    running,
    start_slow_reply,
    user_texts,
    wait_until,
)


def pending_rows(client: TestClient, queue: str) -> list[dict]:
    return [row for row in client.get(queue).json() if row["send_now_pending"]]


def test_send_now_of_a_queued_compact_summarises_and_empties_the_queue(client: TestClient, fake: FakeBackend) -> None:
    conversation_id = conversation_with_turns(client, fake)
    queue = queue_url(conversation_id)
    item = client.post(queue, json={"text": "/compact keep the API decisions"}).json()

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = start_slow_reply(client, fake, pool, conversation_id)
        # Added only now: added before, the running reply could read it instead of the slow one.
        fake.responses.append(ScriptedResponse(text="Summary after the stop."))
        assert client.post(f"{queue}/send-now", json={"item_id": item["id"]}).status_code == 202
        assert first.result(timeout=10).status_code == 200

    wait_until(lambda: summaries(client, conversation_id) and not running(client, conversation_id))
    wait_until(lambda: client.get(queue).json() == [])
    assert summaries(client, conversation_id) == ["Summary after the stop."]
    assert pending_rows(client, queue) == []


def test_send_now_of_a_message_and_a_command_leaves_no_row_pending(client: TestClient, fake: FakeBackend) -> None:
    conversation_id = conversation_with_turns(client, fake)
    queue = queue_url(conversation_id)
    message = client.post(queue, json={"text": "Then say this"}).json()
    command = client.post(queue, json={"text": "/compact"}).json()

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = start_slow_reply(client, fake, pool, conversation_id)
        fake.responses.extend([ScriptedResponse(text="Handled."), ScriptedResponse(text="Summary.")])
        codes = [
            client.post(f"{queue}/send-now", json={"item_id": item["id"]}).status_code for item in (message, command)
        ]
        assert first.result(timeout=10).status_code == 200

    assert codes[0] == 202
    # Whatever the second request got, the handoff has ended: no reply runs and no row waits for a job.
    wait_until(lambda: not running(client, conversation_id) and not pending_rows(client, queue))
    remaining = [row["id"] for row in client.get(queue).json()]
    handled = "Then say this" in user_texts(client, conversation_id)
    assert handled or message["id"] in remaining
    assert bool(summaries(client, conversation_id)) or command["id"] in remaining
    assert pending_rows(client, queue) == []
