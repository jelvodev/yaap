"""The audit log: one row per model request that leaves YAAP, per environment, kept a year."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.agent.runs import RunConfig, RunRegistry, RunServices
from app.agent.gate import call_tool
from app.agent.tools import tools_for
from app.a2a.tool import agent_tools
from app.core.audit import ProjectData, carried_data
from app.providers.base import AgentRequest, HistoryMessage, SystemPart, ToolServer
from app.providers.fake_provider import ScriptedResponse
from app.scheduling.service import tick as tick_now
from app.storage.models import AuditRecord
from tests.conftest import parse_events
from tests.test_a2a import FakeAgent, configure_agent
from tests.test_attachments import png_bytes, upload
from tests.test_chat import add_key, start_conversation
from tests.test_cli_workers import delegate_to, use_fake_cli
from tests.test_delegation import project_conversation

SECRET_VALUE = "sup3r-s3cret-value-987"


def rows(client, environment_id: int | None = None) -> list[AuditRecord]:
    with client.app.state.session_factory() as session:
        query = select(AuditRecord).order_by(AuditRecord.id)
        if environment_id is not None:
            query = query.where(AuditRecord.environment_id == environment_id)
        found = list(session.scalars(query))
        session.expunge_all()
    return found


def send(client, conversation_id: int, text: str, **extra) -> list[tuple[str, dict]]:
    response = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": text, **extra})
    assert response.status_code == 200, response.text
    return parse_events(response.text)


def request_with(messages: list[HistoryMessage], tools: bool = False) -> AgentRequest:
    return AgentRequest(
        model="m",
        effort="low",
        system=[SystemPart("Use {{secret:NAME}} in commands.")],
        messages=messages,
        tool_server=ToolServer("http://x/mcp", "t") if tools else None,
    )


TEXT = [HistoryMessage("user", [{"type": "text", "text": "Hello"}])]
WITH_PLACEHOLDER = [HistoryMessage("user", [{"type": "text", "text": "Deploy with {{secret:DEPLOY_KEY}}"}])]
WITH_TOOL_CALL = [
    HistoryMessage("user", [{"type": "text", "text": "Read it"}]),
    HistoryMessage("assistant", [{"type": "tool_call", "name": "read_file", "input": {}, "output": "x"}]),
]


@pytest.mark.parametrize(
    ("messages", "tools", "conversation", "project", "attachments", "known", "classes", "names"),
    [
        (TEXT, False, True, ProjectData(), False, (), ("conversation",), ()),
        (TEXT, True, True, ProjectData(), False, (), ("conversation", "files"), ()),
        (WITH_TOOL_CALL, False, True, ProjectData(), False, (), ("conversation", "files"), ()),
        (TEXT, False, True, ProjectData(), True, (), ("conversation", "attachments"), ()),
        (
            TEXT,
            True,
            True,
            ProjectData(memory=True, knowledge=True, secret_names=("API_TOKEN",)),
            False,
            (),
            ("conversation", "files", "knowledge", "memory", "secrets"),
            ("API_TOKEN",),
        ),
        # Placeholders in the messages count; the system prompt's own example ({{secret:NAME}}) does not.
        (WITH_PLACEHOLDER, False, False, ProjectData(), False, (), ("secrets",), ("DEPLOY_KEY",)),
        (TEXT, False, False, ProjectData(), False, ("memory",), ("memory",), ()),
        (TEXT, False, False, ProjectData(), False, (), (), ()),
    ],
)
def test_the_data_classes_follow_what_the_request_carries(
    messages, tools, conversation, project, attachments, known, classes, names
):
    request = request_with(messages, tools)
    assert carried_data(request, conversation, project, attachments, known) == (classes, names)


def test_a_reply_in_a_project_writes_one_row_with_its_data(client, fake, tmp_path):
    project, conversation_id = project_conversation(client)
    assert client.put("/api/v1/environments/1/secrets/API_TOKEN", json={"value": SECRET_VALUE}).status_code == 200
    memory = Path(tmp_path) / "workspace" / project["folder"] / ".blueprint" / "memory.md"
    memory.parent.mkdir(parents=True, exist_ok=True)
    memory.write_text("# Memory\n\n## Decisions\n\n- Use tabs.\n", encoding="utf-8")
    fake.responses.append(ScriptedResponse(text="Hi."))

    send(client, conversation_id, "Hello")

    (row,) = rows(client)
    assert (row.purpose, row.environment_id, row.project_id, row.conversation_id) == ("reply", 1, project["id"], conversation_id)
    assert (row.provider, row.connection, row.model, row.task) == ("anthropic", "api_key", "claude-opus-5-5", None)
    assert row.data_classes == ["conversation", "files", "memory", "secrets"]
    assert row.secret_names == ["API_TOKEN"] and row.outcome == "sent" and row.reason is None


def test_a_reply_with_an_attachment_records_the_attachment(client, fake):
    add_key(client)
    conversation_id = start_conversation(client)
    image = upload(client, conversation_id, "shot.png", png_bytes()).json()
    fake.responses.append(ScriptedResponse(text="A red square."))

    send(client, conversation_id, "What is this?", attachment_ids=[image["id"]])

    (row,) = rows(client)
    assert row.purpose == "reply" and row.project_id is None
    assert "attachments" in row.data_classes and "conversation" in row.data_classes


def test_a_sub_run_gets_its_own_row_without_the_conversation(client, fake):
    project, conversation_id = project_conversation(client)
    fake.responses.extend(
        [
            ScriptedResponse(text="Delegated.", tool_calls=(("delegate", {"task": "Create hello.txt"}),)),
            ScriptedResponse(text="Created.", tool_calls=(("write_file", {"path": "hello.txt", "content": "hi"}),)),
        ]
    )

    send(client, conversation_id, "Make hello")

    reply, sub_run = rows(client)
    assert reply.purpose == "reply"
    assert (sub_run.purpose, sub_run.model, sub_run.connection) == ("sub_run", "claude-sonnet-5-5", "api_key")
    assert (sub_run.project_id, sub_run.conversation_id) == (project["id"], conversation_id)
    assert sub_run.data_classes == ["files"]


def test_a_cli_worker_is_recorded_as_such(client, fake):
    _, conversation_id = project_conversation(client)
    use_fake_cli(client, [("read_file", {"path": "missing.md"})])
    delegate_to(fake, "claude_code", "Look around")

    send(client, conversation_id, "Go")

    _, worker = rows(client)
    assert (worker.purpose, worker.provider, worker.connection) == ("cli_worker", "anthropic", "claude_code")
    assert worker.conversation_id == conversation_id and "files" in worker.data_classes


def test_utility_tasks_are_recorded_by_task_name(client, fake):
    add_key(client)
    client.app.state.utility_titles = True
    conversation_id = start_conversation(client)
    fake.responses.extend([ScriptedResponse(text="Hello there."), ScriptedResponse(text="A greeting")])
    send(client, conversation_id, "Hello")

    project = client.post("/api/v1/environments/1/projects", json={"name": "Site"}).json()
    folder = Path(client.app.state.workspace_dir) / project["folder"]
    (folder / "index.html").write_text("<h1>Hi</h1>\n", encoding="utf-8")
    fake.responses.append(ScriptedResponse(text="Add the home page"))
    repository = f"/api/v1/environments/1/git/repositories/project-{project['id']}"
    assert client.post(f"{repository}/commit-message").status_code == 200

    reply, title, commit = rows(client)
    assert reply.purpose == "reply"
    assert (title.purpose, title.task, title.conversation_id, title.data_classes) == (
        "utility", "title", conversation_id, ["conversation"]
    )
    assert title.model == "claude-haiku-5-5"
    assert (commit.purpose, commit.task, commit.project_id, commit.conversation_id, commit.data_classes) == (
        "utility", "commit_message", project["id"], None, ["files"]
    )


def test_a_call_to_a_remote_agent_is_recorded_with_its_token_by_name(client, monkeypatch, tmp_path):
    configure_agent(client, monkeypatch, FakeAgent())
    with client.app.state.session_factory() as session:
        state = client.app.state
        tools = agent_tools(state.session_factory, state.cipher, state.a2a_transport, session, 1, 7)
    registry = RunRegistry()
    run = registry.start(
        RunConfig(
            conversation_id=7,
            environment_id=1,
            project_id=3,
            project_folder=tmp_path,
            blueprint_layers=[],
            tool_names=tools_for(has_project=True, has_attachments=False),
        ),
        RunServices(extra_tools=tools),
    )

    async def scenario():
        async def approve():
            while True:
                event = await run.events.get()
                if event["event"] == "approval_request":
                    registry.decide(event["approval_id"], "approve")

        approver = asyncio.create_task(approve())
        result = await call_tool(registry, run, "call_agent", {"agent": "helper", "message": "What is new?"})
        approver.cancel()
        return result

    _, is_error = asyncio.run(scenario())

    assert not is_error
    (row,) = rows(client)
    assert (row.purpose, row.provider, row.model, row.project_id, row.conversation_id) == ("a2a", "a2a", "helper", 3, 7)
    assert row.data_classes == ["conversation", "secrets"] and row.secret_names == ["AGENT_TOKEN"]


def test_a_request_in_another_environment_does_not_show_up(client, fake):
    add_key(client, 1)
    add_key(client, 2)
    for environment_id, text in ((1, "In one"), (2, "In two")):
        fake.responses.append(ScriptedResponse(text="Ok."))
        send(client, start_conversation(client, environment_id), text)

    first = client.get("/api/v1/environments/1/audit").json()
    second = client.get("/api/v1/environments/2/audit").json()

    assert first["total"] == 1 and second["total"] == 1
    assert first["entries"][0]["id"] != second["entries"][0]["id"]
    exported = client.get("/api/v1/environments/1/audit/export", params={"format": "json"}).json()
    assert len(exported) == 1


def test_the_log_filters_by_period_project_model_and_purpose(client, fake):
    project, conversation_id = project_conversation(client)
    fake.responses.extend(
        [
            ScriptedResponse(text="Delegated.", tool_calls=(("delegate", {"task": "Do it"}),)),
            ScriptedResponse(text="Done."),
        ]
    )
    send(client, conversation_id, "Go")
    url ="/api/v1/environments/1/audit"
    today = datetime.now(UTC).date()

    assert client.get(url).json()["models"] == ["claude-opus-5-5", "claude-sonnet-5-5"]
    assert [entry["purpose"] for entry in client.get(url, params={"purpose": "sub_run"}).json()["entries"]] == ["sub_run"]
    assert client.get(url, params={"model": "claude-opus-5-5"}).json()["total"] == 1
    assert client.get(url, params={"project_id": project["id"]}).json()["total"] == 2
    assert client.get(url, params={"project_id": project["id"] + 1}).json()["total"] == 0
    assert client.get(url, params={"start": str(today), "end": str(today)}).json()["total"] == 2
    assert client.get(url, params={"end": str(today - timedelta(days=1))}).json()["total"] == 0
    assert client.get(url, params={"start": str(today), "end": str(today - timedelta(days=1))}).status_code == 422
    entry = client.get(url).json()["entries"][0]
    assert entry["project"] == "Shop"


def test_rows_older_than_a_year_are_removed_by_the_minute_loop(client):
    now = datetime.now(UTC)
    with client.app.state.session_factory() as session:
        for age in (timedelta(days=366), timedelta(days=364)):
            session.add(
                AuditRecord(
                    created_at=now - age,
                    environment_id=1,
                    purpose="reply",
                    provider="anthropic",
                    connection="api_key",
                    model="claude-opus-5-5",
                    data_classes=["conversation"],
                    secret_names=[],
                    outcome="sent",
                )
            )
        session.commit()

    import anyio

    client.app.state.portal.call(anyio.to_thread.run_sync, tick_now, client.app)

    (kept,) = rows(client)
    assert now - kept.created_at < timedelta(days=365)


def test_the_export_holds_no_secret_values(client, fake):
    _, conversation_id = project_conversation(client)
    assert client.put("/api/v1/environments/1/secrets/API_TOKEN", json={"value": SECRET_VALUE}).status_code == 200
    fake.responses.append(ScriptedResponse(text=f"I saw {SECRET_VALUE}."))

    # The value itself in the first message (which also becomes the title) and the placeholder next to it.
    send(client, conversation_id, f"Log in with {SECRET_VALUE} or {{{{secret:API_TOKEN}}}}")

    as_csv = client.get("/api/v1/environments/1/audit/export", params={"format": "csv"})
    as_json = client.get("/api/v1/environments/1/audit/export", params={"format": "json"})
    listed = client.get("/api/v1/environments/1/audit")
    assert as_csv.status_code == 200 and as_csv.headers["content-type"].startswith("text/csv")
    assert as_csv.headers["content-disposition"].startswith('attachment; filename="yaap-audit-')
    for body in (as_csv.text, as_json.text, listed.text):
        assert SECRET_VALUE not in body
        assert "API_TOKEN" in body
    (record,) = json.loads(as_json.text)
    assert record["secret_names"] == ["API_TOKEN"] and "secrets" in record["data_classes"]
    assert as_csv.text.splitlines()[0].startswith("time,purpose,task,project_id,project,conversation_id")


def row_text(row: AuditRecord) -> str:
    return json.dumps([getattr(row, column.name) for column in AuditRecord.__table__.columns], default=str)


def test_a_request_blocked_by_an_outgoing_check_is_recorded_as_refused_by_the_secrets_name(client, fake):
    add_key(client)
    assert client.put("/api/v1/environments/1/secrets/API_TOKEN", json={"value": SECRET_VALUE}).status_code == 200
    conversation_id = start_conversation(client)

    send(client, conversation_id, f"Log in with {SECRET_VALUE}")

    (row,) = rows(client)
    assert fake.requests == []
    assert (row.purpose, row.outcome) == ("reply", "refused")
    assert row.reason is not None and "the secret API_TOKEN" in row.reason
    assert SECRET_VALUE not in row_text(row)


def test_a_reply_refused_by_the_model_policy_is_recorded_as_refused(client, fake):
    add_key(client)
    conversation_id = start_conversation(client)
    policy = {"value": {"models": ["claude-haiku-5-5"]}}
    assert client.put("/api/v1/environments/1/settings/model_policy", json=policy).status_code == 200

    response = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Hello"})

    assert response.status_code != 200 and "Claude Opus 5.5 is not allowed" in response.text
    (row,) = rows(client)
    assert fake.requests == []
    assert (row.purpose, row.model, row.conversation_id, row.outcome) == (
        "reply", "claude-opus-5-5", conversation_id, "refused"
    )
    assert row.data_classes == ["conversation"]
    assert row.reason is not None and "Refused by the model policy of the environment" in row.reason


def test_a_sub_run_refused_by_the_model_policy_is_recorded_as_refused(client, fake):
    _, conversation_id = project_conversation(client)
    assert client.put("/api/v1/environments/1/secrets/API_TOKEN", json={"value": SECRET_VALUE}).status_code == 200
    # The reply's model is allowed, the preset's worker model (Sonnet) is not.
    policy = {"value": {"models": ["claude-opus-5-5"]}}
    assert client.put("/api/v1/environments/1/settings/model_policy", json=policy).status_code == 200
    fake.responses.append(ScriptedResponse(text="Delegated.", tool_calls=(("delegate", {"task": "Deploy the site"}),)))

    events = send(client, conversation_id, "Go")

    (result,) = [data for name, data in events if name == "tool_result"]
    assert result["is_error"] and "Claude Sonnet 5.5 is not allowed" in result["output"]
    reply, sub_run = rows(client)
    assert (reply.purpose, reply.outcome) == ("reply", "sent")
    assert (sub_run.purpose, sub_run.model, sub_run.outcome) == ("sub_run", "claude-sonnet-5-5", "refused")
    assert sub_run.reason is not None and "Claude Sonnet 5.5 is not allowed" in sub_run.reason
    assert sub_run.data_classes == ["files", "secrets"] and sub_run.secret_names == ["API_TOKEN"]
    assert SECRET_VALUE not in row_text(sub_run)


def test_a_utility_task_refused_by_the_model_policy_is_recorded_as_refused(client, fake):
    add_key(client)
    local_only = {"value": {"models": "local"}}
    assert client.put("/api/v1/environments/1/settings/model_policy", json=local_only).status_code == 200
    project = client.post("/api/v1/environments/1/projects", json={"name": "Site"}).json()
    folder = Path(client.app.state.workspace_dir) / project["folder"]
    (folder / "index.html").write_text("<h1>Hi</h1>\n", encoding="utf-8")
    repository = f"/api/v1/environments/1/git/repositories/project-{project['id']}"

    assert client.post(f"{repository}/commit-message").status_code == 409

    (row,) = rows(client)
    assert fake.requests == []
    assert (row.purpose, row.task, row.project_id) == ("utility", "commit_message", project["id"])
    assert row.outcome == "refused"
    assert row.reason is not None and "only local models" in row.reason
