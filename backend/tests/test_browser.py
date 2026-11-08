"""The agent's browser, with a stand-in for Playwright MCP."""

import sys
from pathlib import Path

from app.providers.fake_provider import ScriptedResponse
from tests.test_chat import send
from tests.test_delegation import project_conversation

SERVER = str(Path(__file__).with_name("browser_test_server.py"))


def with_browser(client) -> None:
    client.app.state.browser_command = [sys.executable, SERVER]


def results(events) -> list[dict]:
    return [data for name, data in events if name == "tool_result"]


def test_local_pages_open_without_asking_and_tools_are_narrowed(client, fake):
    with_browser(client)
    _, conversation_id = project_conversation(client)
    fake.responses.append(
        ScriptedResponse(
            text="Checked.",
            tool_calls=(
                ("open_browser", {}),
                ("browser__browser_navigate", {"url": "http://localhost:5173/"}),
                ("browser__browser_click", {"ref": "e3"}),
                ("browser__browser_take_screenshot", {}),
            ),
        )
    )
    events = send(client, conversation_id, "Check the app")
    # After open_browser the run offers the browser's tools, narrowed to the allowed ones.
    names = [tool.name for tool in fake.requests[0].tool_box.definitions]
    assert "open_browser" in names and "browser__browser_navigate" in names
    assert "browser__browser_evaluate" not in names and "browser__browser_file_upload" not in names
    assert not any(name == "approval_request" for name, _ in events)
    outputs = results(events)
    assert "browser__browser_navigate" in outputs[0]["output"]
    assert "Page URL: http://localhost:5173/" in outputs[1]["output"]
    assert outputs[3]["output"].endswith("[Screenshot: shot 1]")
    shot = client.get(f"/api/v1/conversations/{conversation_id}/browser/shots/1")
    assert shot.status_code == 200 and shot.content.startswith(b"\x89PNG")
    # Removing the conversation removes its browser and screenshots.
    folder = Path(client.app.state.data_dir) / "browser" / str(conversation_id)
    assert folder.exists()
    assert client.delete(f"/api/v1/conversations/{conversation_id}").status_code == 204
    assert not folder.exists()


def test_other_sites_ask_count_as_outside_content_and_take_secrets(tmp_path):
    import asyncio

    from app.agent.runs import RunConfig, RunRegistry, RunServices
    from app.agent.gate import call_tool
    from app.agent.tools import tools_for
    from app.browser.service import browser_tools
    from app.mcp_client.manager import McpManager

    async def scenario():
        manager = McpManager()
        registry = RunRegistry()
        run = registry.start(
            RunConfig(conversation_id=7, environment_id=1, blueprint_layers=[], tool_names=tools_for(False, False)),
            RunServices(
                secrets={"SITE_PASSWORD": "hunter2-secret"},
                extra_tools=await browser_tools(manager, [sys.executable, SERVER], tmp_path, 7),
            ),
        )
        asked = []

        async def approve_all():
            while True:
                event = await run.events.get()
                if event["event"] == "approval_request":
                    asked.append(event)
                    registry.decide(event["approval_id"], "approve")

        approver = asyncio.create_task(approve_all())
        output, is_error = await call_tool(registry, run, "browser__browser_navigate", {"url": "https://example.com/login"})
        assert not is_error and asked[-1]["summary"] == "Open in the browser: https://example.com/login"
        assert run.state.outside_sources[-1].label == "web page https://example.com/login"
        typed, is_error = await call_tool(
            registry, run, "browser__browser_type", {"ref": "e2", "text": "{{secret:SITE_PASSWORD}}"}
        )
        assert not is_error and "hunter2-secret" not in typed and "[secret:SITE_PASSWORD]" in typed
        assert "SITE_PASSWORD" in asked[-1]["summary"]  # A secret always asks.
        assert "hunter2-secret" not in asked[-1]["detail"]
        refused, is_error = await call_tool(registry, run, "browser__browser_click", {"ref": "{{secret:SITE_PASSWORD}}"})
        assert is_error and "Secret placeholders work only" in refused
        approver.cancel()
        await manager.shutdown()

    asyncio.run(scenario())
