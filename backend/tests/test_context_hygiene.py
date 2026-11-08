"""Context hygiene: skills after a summary, the per-message context, trimming and loop detection."""

import asyncio
from pathlib import Path

from sqlalchemy import select

from app.agent.runs import RunConfig, RunRegistry, RunServices
from app.agent.gate import call_tool
from app.agent.tools import tools_for
from app.core.goals import GoalHandle
from app.core.summaries import apply_summary, carried
from app.core.trimming import KEEP_CHARS, apply_trim, shrink_raw, trim_point
from app.providers.base import HistoryMessage, TokenUsage
from app.providers.fake_provider import ScriptedResponse
from app.storage.models import Conversation, Goal, Message
from tests.test_chat import add_key, send, start_conversation
from tests.test_delegation import project_conversation

LONG = "x" * 5000


def message(role: str, content: list[dict], raw: list[dict] | None = None) -> Message:
    return Message(role=role, content=content, provider_raw=raw if raw is not None else ([{"type": "text", "text": "ok"}] if role == "assistant" else None), status="complete")


def turn(question: str, answer: list[dict], raw: list[dict] | None = None) -> list[Message]:
    return [message("user", [{"type": "text", "text": question}]), message("assistant", answer, raw)]


def history_of(branch: list[Message]) -> list[HistoryMessage]:
    return [HistoryMessage(role=item.role, content=item.content, provider_raw=item.provider_raw) for item in branch]


def test_pinned_and_loaded_skills_come_back_after_a_summary():
    branch = [
        message("user", [{"type": "skill", "name": "caveman", "text": "Talk short."}, {"type": "skill", "name": "old", "text": "Gone."}, {"type": "text", "text": "Hi"}]),
        message("assistant", [{"type": "tool_call", "name": "load_standard", "input": {"name": "coding"}, "output": "Use types."}]),
        message("user", [{"type": "skill_removed", "name": "old"}, {"type": "text", "text": "Next"}]),
        message("assistant", [{"type": "text", "text": "Ok"}, {"type": "summary", "text": "Earlier: greetings."}]),
        message("user", [{"type": "text", "text": "Now?"}]),
    ]
    kept = carried(branch[:4])
    assert kept.pinned == {"caveman": "Talk short."}
    assert kept.loaded == [("load_standard", "coding", "Use types.")]

    history = apply_summary(branch, history_of(branch))
    assert len(history) == 1
    texts = [block["text"] for block in history[0].content]
    assert texts[0].endswith("Earlier: greetings.")
    assert '<skill name="caveman">\nTalk short.\n</skill>' in texts
    assert any(text.startswith('<standard name="coding" loaded="earlier">') for text in texts)
    assert texts[-1] == "Now?"
    assert not any("Gone." in text for text in texts)


def test_loaded_skills_over_the_budget_are_named_instead():
    big = "y" * 30_000
    covered = [
        message("assistant", [
            {"type": "tool_call", "name": "load_skill", "input": {"name": "first"}, "output": big},
            {"type": "tool_call", "name": "load_skill", "input": {"name": "second"}, "output": big},
        ])
    ]
    kept = carried(covered)
    assert [name for _, name, _ in kept.loaded] == ["second"] and kept.dropped == ["first"]


def test_the_message_carries_the_date_in_the_users_time_zone(client, fake):
    add_key(client)
    assert client.put("/api/v1/settings/timezone", json={"value": "Mars/Base"}).status_code == 422
    assert client.put("/api/v1/settings/timezone", json={"value": "Europe/Amsterdam"}).status_code == 200
    conversation_id = start_conversation(client)
    fake.responses.extend([ScriptedResponse(text="One."), ScriptedResponse(text="Two.")])
    send(client, conversation_id, "First")
    send(client, conversation_id, "Second")
    last = fake.requests[-1].messages[-1].content
    assert last[0]["text"] == "Second" and "(Europe/Amsterdam)" in last[-1]["text"]
    assert last[-1]["text"].startswith("[Context for this message]\nDate and time: ")
    # Only the newest message carries it; the system prompt stays the same between messages.
    earlier = fake.requests[-1].messages[0].content
    assert all("Date and time" not in block.get("text", "") for block in earlier)
    assert [part.text for part in fake.requests[0].system] == [part.text for part in fake.requests[1].system]


def test_tool_results_are_shortened_in_both_provider_formats():
    anthropic = [{"type": "anthropic_turns", "messages": [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "a", "name": "read_file", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a", "content": [{"type": "text", "text": LONG}]}]},
        {"role": "assistant", "content": [{"type": "text", "text": "short"}]},
    ]}]
    shrunk = shrink_raw(anthropic)
    result_text = shrunk[0]["messages"][1]["content"][0]["content"][0]["text"]
    assert len(result_text) < 2 * KEEP_CHARS + 100 and "left out to save context" in result_text
    assert anthropic[0]["messages"][1]["content"][0]["content"][0]["text"] == LONG  # The stored reply is untouched.
    openai = [{"type": "openai_items", "items": [{"type": "function_call_output", "call_id": "b", "output": LONG}, {"type": "message", "content": "x"}]}]
    assert "left out" in shrink_raw(openai)[0]["items"][0]["output"]
    assert shrink_raw(openai)[0]["items"][1] == {"type": "message", "content": "x"}


def test_the_trim_point_leaves_the_last_turns_whole():
    tool = [{"type": "tool_call", "name": "read_file", "input": {}, "output": LONG}]
    raw = [{"type": "openai_items", "items": [{"type": "function_call_output", "call_id": "c", "output": LONG}]}]
    branch = [item for number in range(6) for item in turn(f"Q{number}", tool, raw)]
    point = trim_point(branch)
    assert point == 3  # The end of the second turn; the last four stay whole.
    branch[point].content = [*branch[point].content, {"type": "trim"}]
    history = apply_trim(branch, history_of(branch))
    outputs = [item.provider_raw[0]["items"][0]["output"] for item in history if item.role == "assistant"]
    assert ["left out" in output for output in outputs] == [True, True, False, False, False, False]
    assert trim_point(branch) is None  # Nothing new to shorten yet.


def test_a_reply_past_half_the_window_marks_a_trim(client, fake):
    client.app.state.background_summaries = False
    project, conversation_id = project_conversation(client)
    folder = Path(client.app.state.workspace_dir) / project["folder"]
    (folder / "big.txt").write_text(LONG, encoding="utf-8")
    window = client.app.state.providers.find_model("claude-opus-5-5").context_window
    fake.responses.extend([ScriptedResponse(text="Read.", tool_calls=(("read_file", {"path": "big.txt"}),)), ScriptedResponse(text="Done.")])
    send(client, conversation_id, "Read big.txt")
    for number in range(4):
        fake.responses.append(ScriptedResponse(text=f"Answer {number}.", usage=TokenUsage(input_tokens=int(window * 0.55))))
        send(client, conversation_id, f"Question {number}?")
    with client.app.state.session_factory() as session:
        messages = session.scalars(select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id)).all()
        marked = [item.id for item in messages if any(block.get("type") == "trim" for block in item.content)]
    assert marked == [messages[1].id]
    breakdown = client.get(f"/api/v1/conversations/{conversation_id}/context").json()
    assert any(part["label"].startswith("Old tool output shortened") for part in breakdown["parts"])


def test_repeated_identical_calls_are_warned_then_refused_and_pause_the_goal(client):
    project = client.post("/api/v1/environments/1/projects", json={"name": "Site"}).json()
    folder = Path(client.app.state.workspace_dir) / project["folder"]
    conversation_id = start_conversation(client)
    with client.app.state.session_factory() as session:
        session.get_one(Conversation, conversation_id).project_id = project["id"]
        goal = Goal(conversation_id=conversation_id, objective="x", status="active", max_turns=5, max_cost=0, turns=1)
        session.add(goal)
        session.commit()
        goal_id = goal.id
    registry = RunRegistry()
    run = registry.start(RunConfig(
        conversation_id=conversation_id,
        environment_id=1,
        blueprint_layers=[],
        tool_names=tools_for(has_project=True, has_attachments=False),
        project_id=project["id"],
        project_folder=folder,
    ), RunServices(goal=GoalHandle(goal_id, client.app.state.session_factory)))
    outputs = [asyncio.run(call_tool(registry, run, "list_files", {"path": "."})) for _ in range(5)]
    assert "Note from YAAP" not in outputs[1][0] and "Note from YAAP" in outputs[2][0]
    assert outputs[4][1] and outputs[4][0].startswith("Refused: this is call 5")
    with client.app.state.session_factory() as session:
        assert session.get_one(Goal, goal_id).status == "paused"
    # Another call in between starts the count again.
    asyncio.run(call_tool(registry, run, "git_status", {}))
    assert "Note from YAAP" not in asyncio.run(call_tool(registry, run, "list_files", {"path": "."}))[0]
