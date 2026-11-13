"""Delegation: a run hands a task to a sub-run on the worker model, which reports back."""

from pathlib import Path

from sqlalchemy import select

from app.providers.base import TokenUsage
from app.providers.fake_provider import ScriptedResponse
from app.storage.models import AgentRunRecord, UsageRecord
from tests.conftest import parse_events
from tests.test_chat import add_key


def project_conversation(client, preset: str | None = "Balanced") -> tuple[dict, int]:
    add_key(client)
    project = client.post("/api/v1/environments/1/projects", json={"name": "Shop"}).json()
    conversation_id = client.post(
        "/api/v1/environments/1/conversations",
        json={"model": "claude-opus-5-5", "effort": "medium", "project_id": project["id"], "preset": preset},
    ).json()["id"]
    return project, conversation_id


def test_a_sub_run_does_the_task_on_the_worker_model_and_reports(client, fake, tmp_path):
    project, conversation_id = project_conversation(client)
    fake.responses.extend(
        [
            ScriptedResponse(text="The worker made it.", tool_calls=(("delegate", {"task": "Create hello.txt with hi"}),)),
            ScriptedResponse(
                text="Created hello.txt.",
                tool_calls=(("write_file", {"path": "hello.txt", "content": "hi\n"}),),
                usage=TokenUsage(input_tokens=300, output_tokens=40),
            ),
        ]
    )

    response = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Make hello"})
    events = parse_events(response.text)

    main, worker = fake.requests
    assert main.model == "claude-opus-5-5"
    assert (worker.model, worker.effort) == ("claude-sonnet-5-5", "low")
    assert worker.messages[0].content[0]["text"] == "Create hello.txt with hi"
    assert "# Delegated task" in worker.system[-1].text
    assert worker.max_model_calls == 40
    assert "delegate" not in {tool.name for tool in worker.tool_box.definitions}
    assert "delegate" in {tool.name for tool in main.tool_box.definitions}

    result = next(data for name, data in events if name == "tool_result")
    assert "Sub-run by worker on claude-sonnet-5-5: complete." in result["output"]
    assert "Created hello.txt." in result["output"] and "- hello.txt" in result["output"]
    progress = [data["step"] for name, data in events if name == "delegate_progress"]
    assert progress == ["started", "write_file"]
    assert (Path(tmp_path) / "workspace" / project["folder"] / "hello.txt").read_text() == "hi\n"

    with client.app.state.session_factory() as session:
        runs = list(session.scalars(select(AgentRunRecord).order_by(AgentRunRecord.id)))
        purposes = [record.purpose for record in session.scalars(select(UsageRecord).order_by(UsageRecord.id))]
    parent, child = runs
    assert child.parent_run_id == parent.id and child.task == "Create hello.txt with hi"
    assert child.model == "claude-sonnet-5-5" and child.status == "complete" and child.tool_calls == 1
    assert child.transcript[-1] == {"type": "text", "text": "Created hello.txt."}
    assert purposes == ["worker", "main"]


def test_role_main_and_a_named_agent_choose_other_models(client, fake):
    client.put(
        "/api/v1/blueprint/file",
        params={"path": "agents/builder.md", "environment_id": 1},
        json={"content": "---\ndescription: Builds.\nrole: worker\ntools: [read_file]\n---\nYou build."},
    )
    _, conversation_id = project_conversation(client)
    fake.responses.extend(
        [
            ScriptedResponse(
                text="Done.",
                tool_calls=(
                    ("delegate", {"task": "Think hard", "role": "main"}),
                    ("delegate", {"task": "Build it", "agent": "builder"}),
                    ("delegate", {"task": "Who?", "agent": "nobody"}),
                ),
            ),
            ScriptedResponse(text="Thought."),
            ScriptedResponse(text="Built."),
        ]
    )
    events = parse_events(client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Go"}).text)

    _, main_role, builder = fake.requests
    assert main_role.model == "claude-opus-5-5"
    assert builder.model == "claude-sonnet-5-5"
    assert "You build." in "\n".join(part.text for part in builder.system)
    assert {tool.name for tool in builder.tool_box.definitions} == {"read_file"}
    results = [data for name, data in events if name == "tool_result"]
    assert results[2]["is_error"] and "No agent named 'nobody'" in results[2]["output"]


def test_without_a_worker_the_sub_run_uses_the_conversation_model(client, fake):
    presets = client.get("/api/v1/environments/1/presets").json()
    for preset in presets:
        client.patch(f"/api/v1/presets/{preset['id']}", json={"worker_model": None})
    _, conversation_id = project_conversation(client, preset=None)
    fake.responses.extend(
        [ScriptedResponse(text="Ok.", tool_calls=(("delegate", {"task": "Do it"}),)), ScriptedResponse(text="Did it.")]
    )
    client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Go"})
    assert fake.requests[1].model == "claude-opus-5-5"


def test_the_runs_list_shows_sub_runs_with_their_parent_and_usage_by_role(client, fake):
    _, conversation_id = project_conversation(client)
    fake.responses.extend(
        [ScriptedResponse(text="Ok.", tool_calls=(("delegate", {"task": "List files"}),)), ScriptedResponse(text="Listed.")]
    )
    client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Go"})
    runs = client.get("/api/v1/environments/1/runs").json()
    child, parent = runs
    assert child["parent_run_id"] == parent["id"] and child["task"] == "List files"
    assert child["transcript"] == [{"type": "text", "text": "Listed."}]
    usage = client.get("/api/v1/environments/1/usage/report").json()
    assert {group["label"] for group in usage["by_purpose"]} >= {"Conversations", "Delegated work"}


def test_the_runs_list_filters_on_an_agent_and_keeps_its_sub_runs(client):
    _, conversation_id = project_conversation(client)
    with client.app.state.session_factory() as session:
        reviewer = AgentRunRecord(environment_id=1, conversation_id=conversation_id, agent="reviewer", model="m")
        other = AgentRunRecord(environment_id=1, conversation_id=conversation_id, agent="developer", model="m")
        session.add_all([reviewer, other])
        session.flush()
        session.add(
            AgentRunRecord(environment_id=1, conversation_id=conversation_id, model="m", parent_run_id=reviewer.id, task="Look")
        )
        session.commit()
    runs = client.get("/api/v1/environments/1/runs", params={"agent": "reviewer"}).json()
    assert [(run["agent"], run["task"]) for run in runs] == [(None, "Look"), ("reviewer", None)]
    assert len(client.get("/api/v1/environments/1/runs").json()) == 3


def test_a_work_sub_run_of_a_read_only_run_is_refused_a_write(client, fake, tmp_path):
    project, conversation_id = project_conversation(client)
    client.patch(f"/api/v1/conversations/{conversation_id}", json={"access": "read_only"}).raise_for_status()
    fake.responses.extend(
        [
            ScriptedResponse(text="Asked.", tool_calls=(("delegate", {"task": "Create hello.txt", "mode": "work"}),)),
            ScriptedResponse(text="Tried.", tool_calls=(("write_file", {"path": "hello.txt", "content": "hi\n"}),)),
        ]
    )

    client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Make hello"})

    assert not (Path(tmp_path) / "workspace" / project["folder"] / "hello.txt").exists()
    with client.app.state.session_factory() as session:
        child = session.scalars(select(AgentRunRecord).where(AgentRunRecord.parent_run_id.is_not(None))).one()
    call = next(step for step in child.transcript if step["type"] == "tool_call")
    assert call["is_error"] and "read only" in call["output"]
