"""MCP servers as a source of tools, with a small stdio server from the test suite."""

import asyncio
import sys
import time
from dataclasses import replace
from pathlib import Path

from app.agent.runs import RunConfig, RunRegistry
from app.agent.gate import call_tool
from app.agent.mcp_client_tools import mcp_tool
from app.agent.tools import run_tools, tools_for
from app.mcp_client.config import parse_servers, validate_servers
from app.mcp_client.manager import McpManager, tool_name
from app.providers.fake_provider import ScriptedResponse
from tests.test_chat import send
from tests.test_delegation import project_conversation

SERVER = str(Path(__file__).with_name("mcp_test_server.py"))


def config(**extra) -> dict:
    return {"test": {"transport": "stdio", "command": sys.executable, "args": [SERVER], **extra}}


def test_the_configuration_is_checked():
    assert validate_servers(config()) is None
    assert "needs a command" in validate_servers({"x": {"transport": "stdio"}})
    assert "url" in validate_servers({"x": {"transport": "http", "url": "ftp://nope"}})
    assert "unknown keys" in validate_servers({"x": {"command": "a", "colour": "red"}})
    assert "Server name" in validate_servers({"bad name": {"command": "a"}})
    assert parse_servers({**config(), "off": {"command": "a", "enabled": False}, "gone": None})[0].name == "test"
    assert len(parse_servers({**config(), "off": {"command": "a", "enabled": False}, "gone": None})) == 1
    assert tool_name("test", "hidden thing") == "test__hidden_thing"


def run_with_tools(manager: McpManager, servers) -> tuple[RunRegistry, object]:
    registry = RunRegistry()
    run = registry.start(
        RunConfig(conversation_id=1, environment_id=1, blueprint_layers=[], tool_names=tools_for(False, False))
    )
    tools, errors = asyncio.run(manager.tools(servers))
    assert errors == {}
    run.services = replace(run.services, extra_tools={tool.name: mcp_tool(manager, tool) for tool in tools})
    return registry, run


def test_tools_are_named_disabled_tools_left_out_and_calls_ask():
    manager = McpManager()

    async def scenario():
        servers = parse_servers(config(disabled_tools=["slow"], auto_tools=["env", "fail"]))
        tools, errors = await manager.tools(servers)
        assert errors == {}
        assert [tool.name for tool in tools] == ["test__echo", "test__env", "test__fail", "test__hidden_thing"]
        registry = RunRegistry()
        run = registry.start(
            RunConfig(conversation_id=1, environment_id=1, blueprint_layers=[], tool_names=tools_for(False, False))
        )
        run.services = replace(run.services, extra_tools={tool.name: mcp_tool(manager, tool) for tool in tools})
        assert {"test__echo", "web_fetch"} <= {tool.name for tool in run_tools(run)}

        # Automatic for env, so no approval; its output is outside content.
        output, is_error = await call_tool(registry, run, "test__env", {"name": "PATH"})
        assert not is_error and output and run.state.outside_sources[0].label == "MCP server test"

        async def approve():
            event = (await run.events.get()).model_dump()
            assert event["event"] == "approval_request" and event["summary"].startswith("test: echo")
            registry.decide(event["approval_id"], "approve")

        approver = asyncio.create_task(approve())
        output, is_error = await call_tool(registry, run, "test__echo", {"text": "hi"})
        await approver
        assert not is_error and output.startswith("echo: hi")

        run.state.outside_sources.clear()  # Else the injection guard asks: the run read outside content.
        failed, is_error = await call_tool(registry, run, "test__fail", {})
        assert is_error and "it broke" in failed
        unknown, is_error = await call_tool(registry, run, "test__slow", {})
        assert is_error and "Unknown tool" in unknown
        await manager.shutdown()

    asyncio.run(scenario())


def test_secrets_fill_the_environment_and_are_masked(client):
    project, _ = project_conversation(client)
    saved = client.put("/api/v1/environments/1/secrets/API_TOKEN", params={"project_id": project["id"]}, json={"value": "s3cret-value"})
    assert saved.status_code == 200, saved.text
    servers = {"test": {"transport": "stdio", "command": sys.executable, "args": [SERVER], "env": {"TOKEN": "{{secret:API_TOKEN}}"}, "auto_tools": ["env"]}}
    assert client.put(f"/api/v1/environments/1/settings/mcp_servers", json={"value": servers}).status_code == 200
    listed = client.get("/api/v1/environments/1/mcp", params={"project_id": project["id"]}).json()
    assert listed[0]["state"] == "stopped" and listed[0]["tools"] == []
    tested = client.post("/api/v1/environments/1/mcp/test/test", params={"project_id": project["id"]}).json()
    assert tested["state"] == "running" and "test__echo" in [tool["model_name"] for tool in tested["tools"]]
    assert any("test server starting" in line for line in tested["log"])
    # Without the project, the secret is unknown.
    missing = client.post("/api/v1/environments/1/mcp/test/test")
    assert missing.status_code == 422 and "API_TOKEN" in missing.json()["detail"]


def test_the_agent_uses_mcp_tools_in_a_reply(client, fake):
    project, conversation_id = project_conversation(client)
    servers = config(auto_tools=["env"], env={"GREETING": "hello from env"})
    assert client.put("/api/v1/environments/1/settings/mcp_servers", json={"value": servers}).status_code == 200
    fake.responses.append(ScriptedResponse(text="Read it.", tool_calls=(("test__env", {"name": "GREETING"}),)))
    events = send(client, conversation_id, "What is the greeting?")
    result = next(data for name, data in events if name == "tool_result")
    assert result["output"] == "hello from env" and not result["is_error"]
    assert "test__env" in [tool.name for tool in fake.requests[0].tool_box.definitions]
    # A project switches the server off.
    client.put(f"/api/v1/projects/{project['id']}/settings/mcp_servers", json={"value": {"test": None}})
    fake.responses.append(ScriptedResponse(text="Nothing."))
    send(client, conversation_id, "Again?")
    assert "test__env" not in [tool.name for tool in fake.requests[-1].tool_box.definitions]


def test_timeouts_idle_stop_and_restart():
    manager = McpManager(idle_seconds=0.2)

    async def scenario():
        servers = parse_servers(config(call_timeout=1))
        tools, _ = await manager.tools(servers)
        echo = next(tool for tool in tools if tool.tool == "echo")
        slow = next(tool for tool in tools if tool.tool == "slow")
        from app.mcp_client.client import McpError

        started = time.monotonic()
        try:
            await manager.call(slow, {"seconds": 5})
            raise AssertionError("expected a timeout")
        except McpError as error:
            assert "within 1 seconds" in str(error) and time.monotonic() - started < 4
        first = (await manager.call(echo, {"text": "a"})).text
        await asyncio.sleep(0.3)
        assert await manager.stop_idle() == 1
        connection = manager.connections[echo.key]
        assert not connection.running
        second = (await manager.call(echo, {"text": "b"})).text
        assert connection.running and connection.starts == 3  # Started, again after the timeout, again after idling.
        assert first.split("pid ")[1] != second.split("pid ")[1]
        await manager.shutdown()

    asyncio.run(scenario())


def test_a_server_that_does_not_start_is_reported():
    manager = McpManager()

    async def scenario():
        servers = parse_servers({"broken": {"command": sys.executable, "args": ["-c", "import sys; print('boom', file=sys.stderr)"], "start_timeout": 3}})
        tools, errors = await manager.tools(servers)
        assert tools == [] and "broken" in errors
        await manager.shutdown()

    asyncio.run(scenario())
