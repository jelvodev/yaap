import asyncio
import json
from pathlib import Path

from app.core.chat_events import merge_events
from app.providers.base import (
    AgentRequest,
    Completed,
    HistoryMessage,
    SystemPart,
    TextDelta,
    TokenUsage,
    ToolCallFinished,
    ToolCallStarted,
    ToolServer,
)
from app.providers.claude_code_provider import ClaudeCodeProvider
from app.providers.fake_provider import ScriptedResponse
from tests.conftest import parse_events
from tests.test_claude_code import fake_claude  # noqa: F401 - fixture

AGENT_LINES = [
    {"type": "system", "subtype": "init", "session_id": "agent-session"},
    {
        "type": "assistant",
        "message": {
            "content": [{"type": "tool_use", "id": "toolu_1", "name": "mcp__yaap__read_file", "input": {"path": "app.py"}}],
            "usage": {"input_tokens": 50, "output_tokens": 10, "cache_read_input_tokens": 900, "cache_creation_input_tokens": 0},
        },
        "parent_tool_use_id": None,
    },
    {
        "type": "user",
        "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": [{"type": "text", "text": "print('hi')"}]}]},
        "parent_tool_use_id": None,
    },
    {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "It prints hi."}}, "parent_tool_use_id": None},
    {
        "type": "assistant",
        "message": {
            "content": [{"type": "text", "text": "It prints hi."}],
            "usage": {"input_tokens": 70, "output_tokens": 8, "cache_read_input_tokens": 960, "cache_creation_input_tokens": 0},
        },
        "parent_tool_use_id": None,
    },
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "It prints hi.",
        "session_id": "agent-session",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 120, "output_tokens": 18, "cache_read_input_tokens": 1860, "cache_creation_input_tokens": 0},
    },
]


def test_agent_run_uses_yaap_tools_in_the_project_folder(fake_claude, tmp_path):  # noqa: F811
    fake_claude.set_output(AGENT_LINES)
    project = tmp_path / "project"
    project.mkdir()
    provider = ClaudeCodeProvider(fake_claude.command)
    request = AgentRequest(
        model="claude-opus-5-5",
        effort="medium",
        system=[SystemPart("Rules.")],
        messages=[HistoryMessage(role="user", content=[{"type": "text", "text": "What does app.py do?"}])],
        working_folder=project,
        tool_server=ToolServer(url="http://127.0.0.1:8080/mcp", token="run-secret"),
    )
    config_seen = {}

    async def collect():
        events = []
        try:
            async for event in provider.stream(request):
                if provider.mcp_config_file and not config_seen:
                    config_seen.update(json.loads(provider.mcp_config_file.read_text()))
                events.append(event)
        finally:
            config_file = provider.mcp_config_file
            await provider.close()
            config_seen["removed"] = config_file is not None and not config_file.exists()
        return events

    events = asyncio.run(collect())
    started = [event for event in events if isinstance(event, ToolCallStarted)]
    finished = [event for event in events if isinstance(event, ToolCallFinished)]
    assert started == [ToolCallStarted("toolu_1", "read_file", {"path": "app.py"})]
    assert finished == [ToolCallFinished("toolu_1", "print('hi')", False)]
    completed = events[-1]
    assert isinstance(completed, Completed)
    # The context is the last model call, not the sum of both calls.
    assert completed.context_tokens == 70 + 8 + 960
    assert completed.usage.input_tokens == 120

    log = fake_claude.log()
    arguments = log["argv"]
    assert Path(log["cwd"]) == project
    assert arguments[arguments.index("--allowedTools") + 1] == "mcp__yaap"
    assert arguments[arguments.index("--tools") + 1] == ""
    assert "run-secret" not in " ".join(arguments)
    assert config_seen["mcpServers"]["yaap"]["headers"]["Authorization"] == "Bearer run-secret"
    assert config_seen["removed"] is True


def test_merge_events_interleaves_run_events():
    async def scenario():
        queue: asyncio.Queue = asyncio.Queue()

        async def provider_events():
            yield TextDelta("before ")
            await queue.put({"event": "approval_request", "approval_id": "a1"})
            await asyncio.sleep(0.05)
            yield TextDelta("after")
            yield Completed("end_turn", "m", TokenUsage())

        return [event async for event in merge_events(provider_events(), queue)]

    events = asyncio.run(scenario())
    kinds = [event["event"] if isinstance(event, dict) else type(event).__name__ for event in events]
    assert kinds == ["TextDelta", "approval_request", "TextDelta", "Completed"]


def test_project_conversation_runs_as_an_agent(client, fake, tmp_path):
    client.put("/api/v1/settings/anthropic_connection", json={"value": "claude_code"})
    project = client.post("/api/v1/environments/1/projects", json={"name": "Site"}).json()
    conversation_id = client.post(
        "/api/v1/environments/1/conversations",
        json={"model": "claude-opus-5-5", "effort": "medium", "project_id": project["id"]},
    ).json()["id"]
    fake.responses.append(ScriptedResponse(text="Done."))

    response = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Add a readme"})

    assert parse_events(response.text)[-1][1]["message"]["status"] == "complete"
    request = fake.requests[0]
    assert request.working_folder.resolve() == (Path(tmp_path) / "workspace" / project["folder"]).resolve()
    assert request.tool_server is not None and request.tool_server.url.endswith("/mcp")
    assert '# Project\n\nYou work in the project "Site"' in "\n\n".join(part.text for part in request.system)
    # The run ended with the reply, so its token no longer opens the tools.
    assert client.app.state.runs.find(request.tool_server.token) is None


def test_conversation_outside_a_project_has_no_project_folder(client, fake):
    client.put("/api/v1/settings/anthropic_connection", json={"value": "claude_code"})
    conversation_id = client.post(
        "/api/v1/environments/1/conversations", json={"model": "claude-opus-5-5", "effort": "medium"}
    ).json()["id"]
    fake.responses.append(ScriptedResponse(text="Hi."))
    client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Hallo"})
    # It gets YAAP's tools that need no project (web, skills, standards), but no folder to work in.
    assert fake.requests[0].tool_server is not None and fake.requests[0].working_folder is None
    names = {definition.name for definition in fake.requests[0].tool_box.definitions}
    assert "web_fetch" in names and not names & {"run_command", "write_file", "list_files"}


def test_an_api_key_runs_the_agent_with_tools_in_process(client, fake, tmp_path):
    from tests.test_chat import add_key

    add_key(client)
    project = client.post("/api/v1/environments/1/projects", json={"name": "Keyed"}).json()
    conversation_id = client.post(
        "/api/v1/environments/1/conversations",
        json={"model": "claude-opus-5-5", "effort": "medium", "project_id": project["id"]},
    ).json()["id"]
    fake.responses.append(
        ScriptedResponse(text="Written.", tool_calls=(("write_file", {"path": "hello.txt", "content": "hi\n"}),))
    )

    response = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Write hello"})

    events = parse_events(response.text)
    assert [name for name, _ in events if name.startswith("tool")] == ["tool_call", "tool_result"]
    assert events[-1][1]["message"]["status"] == "complete"
    assert (Path(tmp_path) / "workspace" / project["folder"] / "hello.txt").read_text() == "hi\n"
    request = fake.requests[0]
    assert {tool.name for tool in request.tool_box.definitions} >= {"write_file", "read_file", "run_command"}
    runs = client.get(f"/api/v1/environments/1/runs").json()
    assert runs[0]["tool_calls"] == 1 and runs[0]["status"] == "complete"
    # After the run the tool box refuses further calls.
    import asyncio

    late = asyncio.run(request.tool_box.call("write_file", {"path": "late.txt", "content": "x"}))
    assert late.is_error and not (Path(tmp_path) / "workspace" / project["folder"] / "late.txt").exists()
