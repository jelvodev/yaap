"""A queued /eval starts one run however often it is consumed, and a queued /compact holds the conversation."""

import threading
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor

import anyio
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.chat_events import server_sent_event
from app.core.reply_event_models import CompactedEvent
from app.core.queue_commands import eval_claim
from app.core.reply_commands import QueuedCommand, ReplyCommands
from app.core.reply_errors import CommandRefused
from app.core.reply_services import ReplyServices
from app.evals.runs import RunStart
from app.providers.fake_provider import ScriptedResponse
from app.storage.models import Conversation, EvalRun
from tests.conftest import parse_events
from tests.test_eval_runs import fixture_project, write_case
from tests.test_queue_commands import conversation_with_turns, queue_url, running, start_slow_reply, wait_until


@pytest.mark.parametrize(
    ("item_exists", "existing_run_id", "claimed_by_other", "expected"),
    [
        (True, None, False, "start"),
        (True, None, True, "busy"),
        (False, None, False, "gone"),
        (False, None, True, "gone"),
        (False, 4, False, "existing"),
        (True, 4, False, "existing"),
        (True, 4, True, "existing"),
    ],
)
def test_a_queued_eval_is_started_once_found_again_or_refused(item_exists, existing_run_id, claimed_by_other, expected):
    assert eval_claim(item_exists, existing_run_id, claimed_by_other) == expected


def eval_conversation(client, monkeypatch) -> tuple[int, int]:
    project, folder, commit = fixture_project(client)
    write_case(folder, "greet", commit)
    monkeypatch.setattr(client.app.state.evals, "launch", lambda _run_id: None)
    conversation_id = client.post(
        "/api/v1/environments/1/conversations",
        json={"model": "claude-opus-5-5", "effort": "medium", "project_id": project["id"]},
    ).json()["id"]
    return project["id"], conversation_id


def test_a_second_consume_of_a_queued_eval_returns_its_run_instead_of_starting_another(client, monkeypatch):
    _project_id, conversation_id = eval_conversation(client, monkeypatch)
    queue = queue_url(conversation_id)
    item = client.post(queue, json={"text": "/eval greet"}).json()

    response = client.post(f"{queue}/send", json={"item_id": item["id"]})

    events = parse_events(response.text)
    assert [name for name, _ in events] == ["eval_started"]
    with client.app.state.session_factory() as session:
        (run,) = session.scalars(select(EvalRun)).all()
        assert run.queue_item_id == item["id"]
        assert client.get(queue).json() == []

        # The consumer comes back for the same item, as after a restart: the row is gone, the run is found.
        commands = ReplyCommands(ReplyServices.from_state(client.app.state))
        conversation = session.get_one(Conversation, conversation_id)
        again = commands.queued_eval_run(
            session, client.app.state.evals, conversation, "greet", QueuedCommand((item["id"],))
        )

        assert again[0] == run.id
        assert len(session.scalars(select(EvalRun)).all()) == 1


def test_a_queued_eval_that_is_gone_and_has_no_run_is_refused(client, monkeypatch):
    _project_id, conversation_id = eval_conversation(client, monkeypatch)
    commands = ReplyCommands(ReplyServices.from_state(client.app.state))
    with client.app.state.session_factory() as session:
        conversation = session.get_one(Conversation, conversation_id)
        with pytest.raises(CommandRefused):
            commands.queued_eval_run(session, client.app.state.evals, conversation, None, QueuedCommand((999,)))
        assert session.scalars(select(EvalRun)).all() == []


def slow_compact(monkeypatch, started: threading.Event, release: threading.Event) -> None:
    async def events(_context, _conversation_id, _focus) -> AsyncIterator[str]:
        started.set()
        while not release.is_set():
            await anyio.sleep(0.02)
        yield server_sent_event(CompactedEvent(message_id=1, covered=1, focus=None))

    monkeypatch.setattr("app.core.reply_commands.compact_events", events)


def test_a_reply_cannot_start_while_a_queued_compact_runs_and_can_after_it(client, fake, monkeypatch):
    conversation_id = conversation_with_turns(client, fake)
    queue = queue_url(conversation_id)
    item = client.post(queue, json={"text": "/compact"}).json()
    started, release = threading.Event(), threading.Event()
    slow_compact(monkeypatch, started, release)
    messages = f"/api/v1/conversations/{conversation_id}/messages"

    with ThreadPoolExecutor(max_workers=1) as pool:
        compacting = pool.submit(client.post, f"{queue}/send", json={"item_id": item["id"]})
        assert started.wait(5)
        assert client.app.state.replies.busy(conversation_id)
        assert client.post(messages, json={"text": "Hello"}).status_code == 409
        release.set()
        assert [name for name, _ in parse_events(compacting.result(timeout=10).text)] == ["compacted"]

    wait_until(lambda: not client.app.state.replies.busy(conversation_id))
    fake.responses.append(ScriptedResponse(text="Answer."))
    assert client.post(messages, json={"text": "Hello"}).status_code == 200


def test_a_queued_compact_is_refused_while_a_reply_runs_and_stays_queued(client, fake):
    conversation_id = conversation_with_turns(client, fake)
    queue = queue_url(conversation_id)
    item = client.post(queue, json={"text": "/compact"}).json()

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = start_slow_reply(client, fake, pool, conversation_id)
        assert running(client, conversation_id)
        response = client.post(f"{queue}/send", json={"item_id": item["id"]})
        assert response.status_code == 409
        assert first.result(timeout=10).status_code == 200

    assert [queued["id"] for queued in client.get(queue).json()] == [item["id"]]


def test_a_queued_eval_created_after_the_first_was_deleted_starts_a_new_run(client, monkeypatch):
    _project_id, conversation_id = eval_conversation(client, monkeypatch)
    queue = queue_url(conversation_id)
    first = client.post(queue, json={"text": "/eval greet"}).json()
    client.post(f"{queue}/send", json={"item_id": first["id"]})
    second = client.post(queue, json={"text": "/eval greet"}).json()

    client.post(f"{queue}/send", json={"item_id": second["id"]})

    assert second["id"] != first["id"]
    with client.app.state.session_factory() as session:
        assert sorted(session.scalars(select(EvalRun.queue_item_id)).all()) == sorted([first["id"], second["id"]])


def test_a_consumer_that_loses_the_race_to_create_the_run_returns_the_winners_run(client, monkeypatch):
    project_id, conversation_id = eval_conversation(client, monkeypatch)
    item = client.post(queue_url(conversation_id), json={"text": "/eval greet"}).json()
    real = client.app.state.evals

    class Racing:
        def start_from_chat(self, conversation, case_name, queue_item_id=None):  # type: ignore[no-untyped-def]
            # The other consumer commits its run and removes the row, then ours hits the unique constraint.
            with client.app.state.session_factory() as other:
                start = RunStart(project_id, "command", case_name, queue_item_id=queue_item_id)
                self.winner = real.create(other, start).id
            raise IntegrityError("insert", {}, Exception("UNIQUE constraint failed: eval_runs.queue_item_id"))

    racing = Racing()
    commands = ReplyCommands(ReplyServices.from_state(client.app.state))
    with client.app.state.session_factory() as session:
        conversation = session.get_one(Conversation, conversation_id)
        run_id, _text = commands.queued_eval_run(session, racing, conversation, "greet", QueuedCommand((item["id"],)))

        assert run_id == racing.winner
        assert len(session.scalars(select(EvalRun)).all()) == 1
