"""/plan: a read-only reply that ends in a draft plan card, and the cases where the message is not /plan."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.blueprint.builtin import instructions
from app.core.reply_errors import CommandRefused
from app.plans.command import MAX_REQUEST, PlanError, plan_block, plan_command
from app.providers.fake_provider import ScriptedResponse
from tests.test_commands_and_skills import last_user_text, put_item, send, start

PLAN_ARGUMENTS: dict[str, Any] = {
    "title": "Health endpoint",
    "summary": "Add GET /health.",
    "risks": ["Auth may block it"],
    "steps": [
        {
            "title": "Add the route",
            "details": "Return ok.",
            "files": ["app.py"],
            "checks": [{"text": "It answers", "run": "pytest -q"}],
        }
    ],
}


def setup_of(project: object | None) -> Any:
    return SimpleNamespace(agent_project=project, layers=[])


@pytest.mark.parametrize("text", ["/plan add a health endpoint", "  /plan   add a health endpoint  ", "/plan\nadd x"])
def test_plan_with_text_is_a_read_only_command_block(text):
    block = plan_block([], text)
    assert block is not None
    assert block["name"] == "plan" and block["type"] == "command" and block["read_only"] is True
    assert block["text"].startswith(instructions("planner", []))
    assert "What to plan:" in block["text"]


@pytest.mark.parametrize("text", ["/planet", "/planning ahead", "please /plan x", "plan x", "/Plan x", ""])
def test_a_message_that_is_not_plan_is_left_alone(text):
    assert plan_block([], text) is None
    assert plan_command(setup_of(object()), text) is None


@pytest.mark.parametrize("text", ["/plan", "/plan   ", "/plan " + "x" * (MAX_REQUEST + 1)])
def test_plan_without_a_request_or_with_too_long_a_request_is_an_error(text):
    with pytest.raises(PlanError):
        plan_block([], text)


def test_plan_outside_a_project_is_refused_with_a_sentence():
    with pytest.raises(CommandRefused, match="project conversation"):
        plan_command(setup_of(None), "/plan x")


def test_plan_alone_in_a_project_is_refused_with_what_to_say():
    with pytest.raises(CommandRefused, match="Say what to plan"):
        plan_command(setup_of(object()), "/plan")


def project_conversation(client) -> tuple[dict, int]:
    project = client.post("/api/v1/environments/1/projects", json={"name": "Site"}).json()
    return project, start(client, project_id=project["id"])


def test_plan_in_the_chat_briefs_the_agent_and_sets_the_reply_read_only(client, fake):
    _, conversation_id = project_conversation(client)
    fake.responses.append(ScriptedResponse(text="No plan yet."))
    send(client, conversation_id, "/plan add a health endpoint")
    text = last_user_text(fake)
    assert "add a health endpoint" in text and "submit_plan" in text
    assert client.get(f"/api/v1/conversations/{conversation_id}").json()["access"] == "default"


def test_plan_outside_a_project_conversation_is_a_409(client):
    conversation_id = start(client)
    refused = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "/plan x"})
    assert refused.status_code == 409 and "project conversation" in refused.json()["detail"]


def test_a_blueprint_command_named_plan_wins_over_the_built_in(client, fake):
    put_item(client, "commands/plan.md", "My own planning.\n")
    _, conversation_id = project_conversation(client)
    fake.responses.append(ScriptedResponse(text="Ok."))
    send(client, conversation_id, "/plan the login page")
    assert last_user_text(fake) == "My own planning.\n\nthe login page"


def test_planet_goes_to_the_model_as_ordinary_text(client, fake):
    _, conversation_id = project_conversation(client)
    fake.responses.append(ScriptedResponse(text="Mars."))
    send(client, conversation_id, "/planet earth")
    assert last_user_text(fake) == "/planet earth"


def snapshot(folder: Path) -> dict[str, bytes]:
    return {str(path.relative_to(folder)): path.read_bytes() for path in folder.rglob("*") if path.is_file()}


def test_plan_reads_a_file_hands_over_the_plan_and_changes_nothing(client, fake, tmp_path):
    project, conversation_id = project_conversation(client)
    folder = Path(tmp_path) / "workspace" / project["folder"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "app.py").write_text("print('hi')\n")
    before = snapshot(folder)
    fake.responses.append(
        ScriptedResponse(
            text="The plan is ready.",
            tool_calls=(("read_file", {"path": "app.py"}), ("submit_plan", PLAN_ARGUMENTS)),
        )
    )

    events = send(client, conversation_id, "/plan add a health endpoint")

    assert not [data for event, data in events if event == "error"], events
    messages = client.get(f"/api/v1/conversations/{conversation_id}").json()["messages"]
    parts = [block for message in messages for block in message["content"] if block["type"] == "plan"]
    assert len(parts) == 1
    plan = client.get(f"/api/v1/plans/{parts[0]['plan_id']}").json()
    assert plan["status"] == "draft" and plan["title"] == "Health endpoint"
    assert snapshot(folder) == before
    assert client.get(f"/api/v1/projects/{project['id']}/checkpoints").json() == []


def test_a_write_during_plan_is_refused_as_read_only(client, fake, tmp_path):
    _, conversation_id = project_conversation(client)
    fake.responses.append(
        ScriptedResponse(text="Done.", tool_calls=(("write_file", {"path": "unsafe.txt", "content": "no"}),))
    )
    events = send(client, conversation_id, "/plan add a health endpoint")
    refusals = [data for event, data in events if event == "tool_result" and data.get("is_error")]
    assert refusals and "read only" in str(refusals[0])
    assert not list(Path(tmp_path).rglob("unsafe.txt"))
