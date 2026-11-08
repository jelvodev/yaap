"""A reply the user stops or that fails: the usage of the model calls it made is stored, and its sub-runs end."""

import threading
import time

import pytest
from sqlalchemy import select

from app.providers.base import TokenUsage
from app.providers.fake_provider import ScriptedResponse
from app.storage.models import AgentRunRecord, Goal, Message, RunStep, UsageRecord
from app.tracing.recorder import ModelStep, ToolStep, TraceRecorder
from tests.test_budget_stop import call_cost, set_budget, wait_until_idle
from tests.test_delegation import project_conversation
from tests.test_plugins import restart

CALL = TokenUsage(input_tokens=1_000, output_tokens=200)
SLOW_TEXT = " ".join(["word"] * 60)


def wait_for_model_steps(client, count: int, seconds: float = 8.0) -> None:
    deadline = time.monotonic() + seconds
    while True:
        with client.app.state.session_factory() as session:
            steps = session.scalars(select(RunStep).where(RunStep.kind == "model")).all()
        if len(steps) >= count:
            return
        assert time.monotonic() < deadline, "the model calls were not recorded"
        time.sleep(0.05)


def start(client, conversation_id: int, text: str, until: str) -> None:
    """Send the message and leave once the given event arrived; the reply goes on in the background."""
    with client.stream("POST", f"/api/v1/conversations/{conversation_id}/messages", json={"text": text}) as response:
        lines = response.iter_lines()
        while next(lines) != f"event: {until}":
            pass


def stop(client, conversation_id: int) -> None:
    assert client.post(f"/api/v1/conversations/{conversation_id}/reply/stop").status_code == 204
    wait_until_idle(client, conversation_id)


def test_a_stopped_reply_stores_the_usage_and_cost_of_its_model_calls(client, fake):
    _, conversation_id = project_conversation(client)
    fake.responses.append(
        ScriptedResponse(text=SLOW_TEXT, delay=0.1, tool_calls=(("list_files", {}),), call_usages=(CALL, CALL))
    )
    start(client, conversation_id, "A long job", "tool_result")
    wait_for_model_steps(client, 1)
    stop(client, conversation_id)

    with client.app.state.session_factory() as session:
        run = session.scalars(select(AgentRunRecord)).one()
        reply = session.scalars(select(Message).where(Message.role == "assistant")).one()
        usage = session.scalars(select(UsageRecord)).one()
    assert reply.status == "incomplete" and run.status == "incomplete" and run.finished_at is not None
    # Only the call that returned before the stop counts; the second never happened.
    assert usage.message_id == reply.id and usage.purpose == "main"
    assert (usage.input_tokens, usage.output_tokens) == (CALL.input_tokens, CALL.output_tokens)
    assert usage.billing == "metered" and usage.cost == pytest.approx(call_cost(client, usage=CALL))


def test_a_reply_stopped_before_any_model_call_returned_stores_no_usage(client, fake):
    _, conversation_id = project_conversation(client)
    fake.responses.append(ScriptedResponse(text=SLOW_TEXT, delay=0.1))
    start(client, conversation_id, "Talk", "text_delta")
    stop(client, conversation_id)

    with client.app.state.session_factory() as session:
        assert session.scalars(select(UsageRecord)).all() == []
        assert session.scalars(select(AgentRunRecord)).one().status == "incomplete"


FAILURE = "The provider broke off the reply."


def failing_after_two_calls() -> ScriptedResponse:
    return ScriptedResponse(
        text="Partly done.", tool_calls=(("list_files", {}),), call_usages=(CALL, CALL), error=FAILURE
    )


@pytest.mark.parametrize(
    ("response", "calls"),
    [
        (failing_after_two_calls(), 2),
        (ScriptedResponse(error=FAILURE), 0),
    ],
)
def test_a_reply_that_fails_on_a_provider_error_stores_the_usage_of_the_calls_that_returned(
    client, fake, response, calls
):
    _, conversation_id = project_conversation(client)
    fake.responses.append(response)
    client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "A long job"})
    wait_until_idle(client, conversation_id)

    with client.app.state.session_factory() as session:
        reply = session.scalars(select(Message).where(Message.role == "assistant")).one()
        usages = session.scalars(select(UsageRecord)).all()
    assert reply.status == "error" and reply.error == FAILURE
    # One record for the reply when any call returned, none otherwise.
    assert len(usages) == (1 if calls else 0)
    assert all(usage.message_id == reply.id and usage.purpose == "main" for usage in usages)
    assert sum(usage.input_tokens for usage in usages) == calls * CALL.input_tokens
    assert sum(usage.output_tokens for usage in usages) == calls * CALL.output_tokens
    assert sum(usage.cost or 0.0 for usage in usages) == pytest.approx(calls * call_cost(client, usage=CALL))


def test_the_usage_of_a_failed_reply_counts_towards_the_budget(client, fake):
    _, conversation_id = project_conversation(client)
    fake.responses.append(failing_after_two_calls())
    client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "A long job"})
    wait_until_idle(client, conversation_id)
    # The two calls of the failed reply are more than this budget allows.
    set_budget(client, call_cost(client, usage=CALL) * 1.5)

    refused = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Try again"})
    assert refused.status_code == 409 and "budget" in refused.json()["detail"]


def test_a_sub_run_of_a_stopped_reply_ends_and_stores_its_usage(client, fake):
    _, conversation_id = project_conversation(client)
    fake.responses.extend(
        [
            ScriptedResponse(
                text="Delegated.", tool_calls=(("delegate", {"task": "Look around"}),), call_usages=(CALL, CALL)
            ),
            ScriptedResponse(text=SLOW_TEXT, delay=0.1, call_usages=(CALL, CALL)),
        ]
    )
    start(client, conversation_id, "Delegate it", "delegate_progress")
    wait_for_model_steps(client, 2)  # the parent's first call and the sub-run's first call
    stop(client, conversation_id)

    with client.app.state.session_factory() as session:
        root = session.scalars(select(AgentRunRecord).where(AgentRunRecord.parent_run_id.is_(None))).one()
        sub_run = session.scalars(select(AgentRunRecord).where(AgentRunRecord.parent_run_id == root.id)).one()
        usages = {record.purpose: record for record in session.scalars(select(UsageRecord))}
    assert root.status == "incomplete"
    assert sub_run.status == "incomplete" and sub_run.finished_at is not None
    assert set(usages) == {"main", "worker"}
    assert usages["main"].input_tokens == CALL.input_tokens
    assert usages["worker"].input_tokens == CALL.input_tokens and usages["worker"].model == sub_run.model
    assert usages["worker"].cost == pytest.approx(call_cost(client, sub_run.model, CALL))


def test_runs_still_running_at_a_restart_end_as_incomplete(client, fake, master_key):
    _, conversation_id = project_conversation(client)
    fake.responses.append(ScriptedResponse(text="Done."))
    client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Go"})
    with client.app.state.session_factory() as session:
        finished = session.scalars(select(AgentRunRecord)).one()
        # A run and its sub-run that were going on when YAAP stopped.
        cut_off = AgentRunRecord(
            environment_id=1, conversation_id=conversation_id, model="claude-opus-5-5", status="running", approvals=[]
        )
        session.add(cut_off)
        session.flush()
        cut_off_sub_run = AgentRunRecord(
            environment_id=1,
            conversation_id=conversation_id,
            model="claude-sonnet-5-5",
            status="running",
            approvals=[],
            parent_run_id=cut_off.id,
        )
        session.add(cut_off_sub_run)
        session.commit()
        ids = (finished.id, cut_off.id, cut_off_sub_run.id)

    with restart(fake, master_key) as restarted:
        with restarted.app.state.session_factory() as session:
            records = [session.get_one(AgentRunRecord, record_id) for record_id in ids]
    assert [record.status for record in records] == ["complete", "incomplete", "incomplete"]
    assert all(record.finished_at is not None for record in records)


def test_the_stop_button_returns_once_the_worker_thread_the_reply_is_in_has_ended(client, fake, monkeypatch):
    _, conversation_id = project_conversation(client)
    in_thread = threading.Event()
    ended: list[str] = []
    write = TraceRecorder.write

    def slow_write(self: TraceRecorder, run_id: int, step: ModelStep | ToolStep) -> None:
        if not in_thread.is_set():
            in_thread.set()
            time.sleep(0.5)
            ended.append("the thread")
        write(self, run_id, step)

    monkeypatch.setattr(TraceRecorder, "write", slow_write)
    fake.responses.append(ScriptedResponse(text=SLOW_TEXT, delay=0.1, tool_calls=(("list_files", {}),)))
    start(client, conversation_id, "A long job", "user_message")
    assert in_thread.wait(5)

    assert client.post(f"/api/v1/conversations/{conversation_id}/reply/stop").status_code == 204

    # Task.cancel() would have returned while the thread still slept; the reply's cancel scope waited for it.
    assert ended == ["the thread"]
    assert client.get("/api/v1/environments/1/replies").json() == []


def test_stopping_a_goal_turn_pauses_the_goal(client, fake):
    _, conversation_id = project_conversation(client)
    fake.responses.append(ScriptedResponse(text=SLOW_TEXT, delay=0.1))
    start(client, conversation_id, "/goal make the tests pass", "text_delta")
    stop(client, conversation_id)

    # The stop reached the goal's follow-up as a cancellation: it paused the goal and sent no next turn.
    with client.app.state.session_factory() as session:
        assert session.scalars(select(Goal)).one().status == "paused"
    assert len(fake.requests) == 1
