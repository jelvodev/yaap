"""Steering a running reply."""

import asyncio

from sqlalchemy import select

from app.agent.runs import RunConfig, RunRegistry
from app.agent.gate import STEERING_NOTE, call_tool
from app.core.message_queue import QueueSteeringHandoff
from app.providers.fake_provider import ScriptedResponse
from app.storage.models import Message, QueuedMessage
from tests.test_agent_tools import project, start_run  # noqa: F401
from tests.test_chat import add_key, send, start_conversation
from tests.test_delegation import project_conversation


def steer_new_runs(monkeypatch, text: str) -> None:
    """Every top-level run starts with a steering message waiting, as if the user sent it right away."""
    original = RunRegistry.start

    def start(self, config, services=None):
        run = original(self, config, services)
        if run.parent_token is None:
            run.state.steering.append(text)
        return run

    monkeypatch.setattr(RunRegistry, "start", start)


def test_the_next_tool_result_carries_the_message(project):  # noqa: F811
    registry = RunRegistry()
    run = start_run(registry, project)
    run.state.steering.append("Use blue, not red.")
    output, _ = asyncio.run(call_tool(registry, run, "list_files", {}))
    assert output.endswith(f"{STEERING_NOTE}\nUse blue, not red.") and run.state.steering == []
    event = run.events.get_nowait().model_dump()
    assert event["event"] == "steering" and event["text"] == "Use blue, not red."
    again, _ = asyncio.run(call_tool(registry, run, "list_files", {}))
    assert STEERING_NOTE not in again


def test_the_reply_marks_where_the_message_arrived(client, fake, monkeypatch):
    project, conversation_id = project_conversation(client)
    steer_new_runs(monkeypatch, "Also add a footer.")
    fake.responses.extend(
        [ScriptedResponse(text="Done.", tool_calls=(("write_file", {"path": "a.html", "content": "<p>hi</p>\n"}),))]
    )
    events = send(client, conversation_id, "Make a page")
    steering = [data for name, data in events if name == "steering"]
    assert [item["text"] for item in steering] == ["Also add a footer."]
    with client.app.state.session_factory() as session:
        reply = session.scalars(select(Message).where(Message.conversation_id == conversation_id, Message.role == "assistant")).one()
        types = [block["type"] for block in reply.content]
    assert types.index("tool_call") < types.index("steering")
    # The model got the message with the tool result.
    result = next(data for name, data in events if name == "tool_result")
    assert result["output"].endswith(f"{STEERING_NOTE}\nAlso add a footer.")
    exported = client.get(f"/api/v1/conversations/{conversation_id}/export").text
    assert "Also add a footer." in exported


def test_an_undelivered_message_is_queued_first(client, fake, monkeypatch):
    _, conversation_id = project_conversation(client)
    client.post(f"/api/v1/conversations/{conversation_id}/queue", json={"text": "Later", "attachment_ids": []})
    steer_new_runs(monkeypatch, "Shorter please.")
    fake.responses.append(ScriptedResponse(text="A long answer."))
    send(client, conversation_id, "Explain")
    queue = client.get(f"/api/v1/conversations/{conversation_id}/queue").json()
    assert [item["text"] for item in queue] == ["Shorter please.", "Later"]


def test_steering_needs_a_running_reply_with_tools(client):
    conversation_id = start_conversation(client)
    response = client.post(f"/api/v1/conversations/{conversation_id}/steer", json={"text": "Hi"})
    assert response.status_code == 409


def test_a_queued_message_can_steer_the_running_reply(client, monkeypatch):
    conversation_id = start_conversation(client)
    item = client.post(f"/api/v1/conversations/{conversation_id}/queue", json={"text": "Skip the tests", "attachment_ids": []}).json()
    run = client.app.state.runs.start(RunConfig(
        conversation_id=conversation_id, environment_id=1, blueprint_layers=[], tool_names=frozenset()
    ))
    run.state.record_id = 42
    monkeypatch.setattr(client.app.state.replies, "running", lambda conversation: object())
    response = client.post(f"/api/v1/conversations/{conversation_id}/steer", json={"item_id": item["id"]})
    assert response.status_code == 202
    with client.app.state.session_factory() as session:
        assert session.scalars(select(QueuedMessage)).one().steering_run_id == 42
    handoff = QueueSteeringHandoff(client.app.state.session_factory)
    assert handoff.claim(conversation_id, 42) == [(item["id"], "Skip the tests")]
    handoff.acknowledge(42, [item["id"]])
    client.app.state.runs.finish(run)
