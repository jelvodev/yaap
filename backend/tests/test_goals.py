"""Goal mode: YAAP keeps a project conversation working towards an objective, turn after turn."""

import time

import pytest

from app.providers.fake_provider import ScriptedResponse
from tests.test_delegation import project_conversation


def wait_for_goal(client, conversation_id: int, statuses: tuple[str, ...], seconds: float = 8.0) -> dict:
    deadline = time.monotonic() + seconds
    while True:
        goal = client.get(f"/api/v1/conversations/{conversation_id}/goal").json()
        running = client.get("/api/v1/environments/1/replies").json()
        if goal and goal["status"] in statuses and conversation_id not in running:
            return goal
        assert time.monotonic() < deadline, f"goal still {goal and goal['status']}"
        time.sleep(0.05)


def test_goal_mode_needs_a_project_and_an_objective(client):
    from tests.test_chat import add_key, start_conversation

    add_key(client)
    plain = start_conversation(client)
    refused = client.post(f"/api/v1/conversations/{plain}/messages", json={"text": "/goal ship it"})
    assert refused.status_code == 409 and "project" in refused.json()["detail"]
    _, conversation_id = project_conversation(client)
    empty = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "/goal"})
    assert empty.status_code == 409 and "objective" in empty.json()["detail"]
    assert client.get(f"/api/v1/conversations/{conversation_id}/goal").json() is None


def test_busy_goal_continuation_does_not_advance_or_stop_the_goal(client):
    from app.core.goals import create_goal
    from app.core.reply_errors import ReplyBusy
    from app.core.reply_service import ReplyService
    from app.core.reply_services import ReplyServices
    from app.storage.models import Conversation, Goal

    _, conversation_id = project_conversation(client)
    with client.app.state.session_factory() as session:
        conversation = session.get_one(Conversation, conversation_id)
        goal = create_goal(session, conversation, "Keep the project stable")
        session.commit()
        goal_id = goal.id

    reservation = client.app.state.replies.reserve(conversation_id)
    try:
        service = ReplyService(ReplyServices.from_state(client.app.state))
        with pytest.raises(ReplyBusy):
            service.send_goal_turn(conversation_id, goal_id=goal_id)
    finally:
        reservation.release()
    with client.app.state.session_factory() as session:
        goal = session.get_one(Goal, goal_id)
        assert (goal.status, goal.turns) == ("active", 1)


def test_yaap_sends_turns_until_the_agent_finishes_the_goal(client, fake):
    _, conversation_id = project_conversation(client)
    fake.responses.extend(
        [
            ScriptedResponse(text="Step one done."),
            ScriptedResponse(text="Step two done."),
            ScriptedResponse(
                text="All tests pass.", tool_calls=(("finish_goal", {"status": "done", "summary": "Tests pass."}),)
            ),
        ]
    )
    response = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "/goal make the tests pass"})
    assert response.status_code == 200

    goal = wait_for_goal(client, conversation_id, ("done",))
    assert goal["turns"] == 3 and goal["summary"] == "Tests pass." and goal["objective"] == "make the tests pass"
    first, second, third = fake.requests
    assert "Goal: make the tests pass" in first.messages[-1].content[0]["text"]
    assert "This is turn 2 of at most 20" in second.messages[-1].content[0]["text"]
    assert "finish_goal" in {tool.name for tool in third.tool_box.definitions}
    messages = client.get(f"/api/v1/conversations/{conversation_id}").json()["messages"]
    users = [message for message in messages if message["role"] == "user"]
    assert users[0]["content"][0]["text"] == "/goal make the tests pass"
    assert [block["turn"] for message in users for block in message["content"] if block["type"] == "goal"] == [1, 2, 3]


def test_the_turn_limit_ends_the_goal_and_the_user_can_stop_it(client, fake):
    _, conversation_id = project_conversation(client)
    fake.responses.extend([ScriptedResponse(text="One."), ScriptedResponse(text="Two.")])
    client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "/goal tidy up"})
    # Lower the limit while the first turn may still run; the second turn is then the last.
    client.patch(f"/api/v1/conversations/{conversation_id}/goal", json={"max_turns": 2})
    goal = wait_for_goal(client, conversation_id, ("limit",))
    assert goal["turns"] == 2 and "2 turns" in goal["summary"]
    assert client.patch(f"/api/v1/conversations/{conversation_id}/goal", json={"status": "stopped"}).status_code == 404

    _, other = project_conversation(client)
    fake.responses.extend([ScriptedResponse(text="Starting.", delay=0.5), ScriptedResponse(text="Never sent.")])
    client.post(f"/api/v1/conversations/{other}/messages", json={"text": "/goal another"})
    stopped = client.patch(f"/api/v1/conversations/{other}/goal", json={"status": "stopped"}).json()
    assert stopped["status"] == "stopped"
    time.sleep(1.0)
    assert len(fake.responses) == 1  # no second turn


def test_a_late_follow_up_leaves_a_newer_goal_alone(client, fake):
    """The follow-up of a reply waits until the reply has left the registry; a scheduled task's next run can start a
    new goal in the same conversation in that moment. The late follow-up must not take that goal for its own."""
    from app.core.chat_history import add_user_message
    from app.core.goals import create_goal, goal_block, start_text
    from app.core.reply_service import ReplyService
    from app.core.reply_services import ReplyServices
    from app.storage.models import Conversation, Goal

    _, conversation_id = project_conversation(client)
    fake.responses.append(
        ScriptedResponse(text="Done.", tool_calls=(("finish_goal", {"status": "done", "summary": "First."}),))
    )
    client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "/goal the first objective"})
    first = wait_for_goal(client, conversation_id, ("done",))
    with client.app.state.session_factory() as session:
        conversation = session.get_one(Conversation, conversation_id)
        second = create_goal(session, conversation, "the second objective")
        block = goal_block(start_text(second.objective, second.max_turns), 1, second.max_turns)
        add_user_message(session, conversation, "", extra_blocks=[block])
        session.commit()
        second_id = second.id

    service = ReplyService(ReplyServices.from_state(client.app.state))
    assert service.send_goal_turn(conversation_id, goal_id=first["id"]) is False
    with client.app.state.session_factory() as session:
        goal = session.get_one(Goal, second_id)
        assert (goal.status, goal.turns) == ("active", 1)  # Not stopped, and no turn sent for it.
    assert len(fake.requests) == 1
