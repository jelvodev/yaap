"""/compact and /eval in the message queue, and the queue across restarts, cancellation and concurrent sends."""

import asyncio
import time
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core.message_queue import QueueError, add_steering, restore_interrupted_handoffs
from app.core.queue_commands import claim_items, queue_item_kind
from app.core.reply_commands import QueuedCommand, claimed_compact_events
from app.core.reply_errors import CommandRefused
from app.providers.fake_provider import ScriptedResponse
from app.storage.models import Message
from tests.conftest import parse_events
from tests.test_chat import add_key, start_conversation
from tests.test_context_control import summaries
from tests.test_delegation import project_conversation
from tests.test_summaries import chat_turns


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("hello", "message"),
        ("/compact", "compact"),
        ("  /compact keep the API decisions ", "compact"),
        ("/eval", "eval"),
        ("/eval date-parser", "eval"),
        ("/eval Not A Case", "message"),
        ("please /compact", "message"),
        ("/compactor", "message"),
        ("/check", "message"),
    ],
)
def test_queue_item_kind_names_the_commands_that_do_not_start_a_reply(text, kind):
    assert queue_item_kind(text) == kind


def queue_url(conversation_id: int) -> str:
    return f"/api/v1/conversations/{conversation_id}/queue"


def conversation_with_turns(client, fake, turns: int = 6) -> int:
    add_key(client)
    conversation_id = start_conversation(client)
    chat_turns(client, fake, conversation_id, turns)
    return conversation_id


def user_texts(client, conversation_id: int) -> list[str]:
    messages = client.get(f"/api/v1/conversations/{conversation_id}").json()["messages"]
    return [
        block["text"] for message in messages if message["role"] == "user" for block in message["content"]
        if block["type"] == "text"
    ]


def wait_until(condition, seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError("The condition did not become true in time.")


class FakeEvals:
    """Stands in for the eval runs' start from the chat; the application keeps the real runs for its shutdown."""

    def __init__(self, client, monkeypatch, refuse: bool = False) -> None:  # type: ignore[no-untyped-def]
        self.started: list[str | None] = []
        self.refuse = refuse
        monkeypatch.setattr(client.app.state.evals, "start_from_chat", self.start_from_chat)

    def start_from_chat(self, _conversation, case_name, queue_item_id=None):  # type: ignore[no-untyped-def]
        if self.refuse:
            raise CommandRefused("No eval cases yet.")
        self.started.append(case_name)
        return 7, "Eval run 7 started."


def test_a_queued_compact_summarises_and_leaves_the_queue(client, fake):
    conversation_id = conversation_with_turns(client, fake)
    queue = queue_url(conversation_id)
    item = client.post(queue, json={"text": "/compact keep the API decisions"}).json()
    assert item["kind"] == "compact"
    fake.responses.append(ScriptedResponse(text="Queued summary."))

    response = client.post(f"{queue}/send", json={"item_id": item["id"]})

    assert [name for name, _ in parse_events(response.text)] == ["compacted"]
    assert summaries(client, conversation_id) == ["Queued summary."]
    assert client.get(queue).json() == []


def test_a_refused_queued_compact_stays_queued_and_unclaimed(client, fake):
    add_key(client)
    conversation_id = start_conversation(client)
    queue = queue_url(conversation_id)
    item = client.post(queue, json={"text": "/compact"}).json()

    response = client.post(f"{queue}/send", json={"item_id": item["id"]})

    assert response.status_code == 409
    assert [(queued["id"], queued["send_now_pending"]) for queued in client.get(queue).json()] == [(item["id"], False)]


def test_a_queued_compact_that_fails_is_reported_and_leaves_the_queue(client, fake):
    conversation_id = conversation_with_turns(client, fake)
    queue = queue_url(conversation_id)
    item = client.post(queue, json={"text": "/compact"}).json()
    fake.responses.append(ScriptedResponse(text=""))

    response = client.post(f"{queue}/send", json={"item_id": item["id"]})

    assert [name for name, _ in parse_events(response.text)] == ["compact_failed"]
    assert client.get(queue).json() == []


async def pending_events(started: asyncio.Event) -> AsyncIterator[str]:
    started.set()
    await asyncio.sleep(60)
    yield "never"


def test_a_queued_command_cut_off_before_it_ends_goes_back_to_the_queue(client):
    conversation_id = start_conversation(client)
    item = client.post(queue_url(conversation_id), json={"text": "/compact"}).json()
    factory = client.app.state.session_factory
    queued = QueuedCommand((item["id"],))

    async def run() -> None:
        started = asyncio.Event()
        stream = claimed_compact_events(factory, conversation_id, queued, pending_events(started))
        task = asyncio.ensure_future(stream.__anext__())
        await started.wait()
        assert client.get(queue_url(conversation_id)).json()[0]["send_now_pending"] is True
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await stream.aclose()

    asyncio.run(run())

    assert [(queued["id"], queued["send_now_pending"]) for queued in client.get(queue_url(conversation_id)).json()] == [
        (item["id"], False)
    ]


def test_a_second_sender_of_the_same_queued_command_does_not_run_it_again(client):
    conversation_id = start_conversation(client)
    item = client.post(queue_url(conversation_id), json={"text": "/compact"}).json()
    factory = client.app.state.session_factory
    ids = (item["id"],)

    assert claim_items(factory, conversation_id, ids, "first", None) is True
    assert claim_items(factory, conversation_id, ids, "second", None) is False
    assert claim_items(factory, conversation_id, (item["id"], 999), "first", "first") is False

    async def run() -> list[str]:
        async def events() -> AsyncIterator[str]:
            yield "ran"

        return [event async for event in claimed_compact_events(factory, conversation_id, QueuedCommand(ids), events())]

    assert [name for name, _ in parse_events("".join(asyncio.run(run())))] == ["compact_failed"]


def test_a_command_claimed_when_yaap_stopped_is_queued_again_after_the_restart(client, fake):
    conversation_id = conversation_with_turns(client, fake)
    queue = queue_url(conversation_id)
    item = client.post(queue, json={"text": "/compact"}).json()
    assert claim_items(client.app.state.session_factory, conversation_id, (item["id"],), "interrupted", None)
    assert client.post(f"{queue}/send", json={"item_id": item["id"]}).status_code == 422

    restore_interrupted_handoffs(client.app.state.session_factory)
    fake.responses.append(ScriptedResponse(text="After the restart."))
    response = client.post(f"{queue}/send", json={"item_id": item["id"]})

    assert [name for name, _ in parse_events(response.text)] == ["compacted"]
    assert client.get(queue).json() == []


def test_send_all_stops_before_a_command_and_sends_a_command_alone(client, fake):
    conversation_id = conversation_with_turns(client, fake, 6)
    queue = queue_url(conversation_id)
    for text in ["First", "Second", "/compact", "Last"]:
        client.post(queue, json={"text": text})
    fake.responses.append(ScriptedResponse(text="Answer."))

    client.post(f"{queue}/send", json={"all": True})

    assert user_texts(client, conversation_id)[-1] == "First\n\nSecond"
    assert [item["text"] for item in client.get(queue).json()] == ["/compact", "Last"]
    fake.responses.append(ScriptedResponse(text="Summary."))
    events = parse_events(client.post(f"{queue}/send", json={"all": True}).text)
    assert [name for name, _ in events] == ["compacted"]
    assert [item["text"] for item in client.get(queue).json()] == ["Last"]


def test_a_queued_command_is_not_given_to_the_running_agent_as_steering(client):
    conversation_id = start_conversation(client)
    item = client.post(queue_url(conversation_id), json={"text": "/eval"}).json()

    with client.app.state.session_factory() as session, pytest.raises(QueueError):
        add_steering(session, conversation_id, 1, "", item["id"])


def test_a_queued_eval_starts_its_run_and_leaves_the_queue(client, monkeypatch):
    evals = FakeEvals(client, monkeypatch)
    _project, conversation_id = project_conversation(client)
    queue = queue_url(conversation_id)
    item = client.post(queue, json={"text": "/eval date-parser"}).json()

    response = client.post(f"{queue}/send", json={"item_id": item["id"]})

    assert [name for name, _ in parse_events(response.text)] == ["eval_started"]
    assert evals.started == ["date-parser"]
    assert client.get(queue).json() == []


def test_a_refused_queued_eval_stays_queued(client, monkeypatch):
    evals = FakeEvals(client, monkeypatch, refuse=True)
    _project, conversation_id = project_conversation(client)
    queue = queue_url(conversation_id)
    item = client.post(queue, json={"text": "/eval"}).json()

    response = client.post(f"{queue}/send", json={"item_id": item["id"]})

    assert response.status_code == 409
    assert [queued["id"] for queued in client.get(queue).json()] == [item["id"]]
    assert evals.started == []


def running(client, conversation_id: int) -> bool:
    return conversation_id in client.get("/api/v1/environments/1/replies").json()


def start_slow_reply(client, fake, pool: ThreadPoolExecutor, conversation_id: int, text: str = "Start"):
    fake.responses.append(ScriptedResponse(text="a slow reply with several words", delay=0.1))
    started = pool.submit(client.post, f"/api/v1/conversations/{conversation_id}/messages", json={"text": text})
    wait_until(lambda: running(client, conversation_id))
    return started


def test_a_queued_eval_sent_now_runs_once_when_the_reply_has_stopped(client, fake, monkeypatch):
    evals = FakeEvals(client, monkeypatch)
    _project, conversation_id = project_conversation(client)
    queue = queue_url(conversation_id)
    item = client.post(queue, json={"text": "/eval"}).json()

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = start_slow_reply(client, fake, pool, conversation_id)
        assert client.post(f"{queue}/send-now", json={"item_id": item["id"]}).status_code == 202
        assert first.result(timeout=10).status_code == 200

    wait_until(lambda: client.get(queue).json() == [] and evals.started)
    time.sleep(0.2)
    assert evals.started == [None]
    assert user_texts(client, conversation_id) == ["Start"]


def test_two_sends_at_once_start_one_reply_and_keep_one_message(client, fake):
    add_key(client)
    conversation_id = start_conversation(client)
    fake.responses.extend([ScriptedResponse(text="one reply with some words", delay=0.1), ScriptedResponse(text="Two.")])
    url = f"/api/v1/conversations/{conversation_id}/messages"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(client.post, url, json={"text": text}) for text in ("Left", "Right")]
        codes = sorted(future.result(timeout=10).status_code for future in futures)

    assert codes == [200, 409]
    assert len(user_texts(client, conversation_id)) == 1


def test_two_sends_of_the_same_queued_message_start_one_reply(client, fake):
    add_key(client)
    conversation_id = start_conversation(client)
    queue = queue_url(conversation_id)
    item = client.post(queue, json={"text": "Only once"}).json()
    fake.responses.extend([ScriptedResponse(text="one reply with some words", delay=0.1), ScriptedResponse(text="Two.")])

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(client.post, f"{queue}/send", json={"item_id": item["id"]}) for _ in range(2)]
        codes = sorted(future.result(timeout=10).status_code for future in futures)

    assert codes[0] == 200 and codes[1] in (409, 422)
    assert user_texts(client, conversation_id) == ["Only once"]
    assert client.get(queue).json() == []


def test_stopping_a_reply_with_a_pending_send_now_still_sends_the_message_once(client, fake):
    add_key(client)
    conversation_id = start_conversation(client)
    queue = queue_url(conversation_id)
    item = client.post(queue, json={"text": "Send me after the stop"}).json()
    fake.responses.append(ScriptedResponse(text="Handed over."))

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = start_slow_reply(client, fake, pool, conversation_id)
        fake.responses.reverse()  # The slow reply is read first, the handed-over reply second.
        assert client.post(f"{queue}/send-now", json={"item_id": item["id"]}).status_code == 202
        client.post(f"/api/v1/conversations/{conversation_id}/reply/stop")
        assert first.result(timeout=10).status_code == 200

    sent = "Send me after the stop"
    wait_until(lambda: sent in user_texts(client, conversation_id) and not running(client, conversation_id))
    assert user_texts(client, conversation_id).count(sent) == 1
    assert client.get(queue).json() == []


def test_a_message_queued_before_a_restart_is_still_queued_and_sendable(client, fake):
    add_key(client)
    conversation_id = start_conversation(client)
    queue = queue_url(conversation_id)
    item = client.post(queue, json={"text": "Survives"}).json()

    restore_interrupted_handoffs(client.app.state.session_factory)
    fake.responses.append(ScriptedResponse(text="Answer."))

    assert [queued["id"] for queued in client.get(queue).json()] == [item["id"]]
    assert client.post(f"{queue}/send", json={"item_id": item["id"]}).status_code == 200
    with client.app.state.session_factory() as session:
        assert session.query(Message).filter(Message.conversation_id == conversation_id).count() == 2
