"""Tools that join a run while it works (efficiency): the browser's tools after open_browser."""

import asyncio
from dataclasses import replace
from typing import Any

import pytest

from app.agent.gate import call_tool
from app.agent.runs import AgentRun, RunConfig, RunRegistry, RunServices
from app.agent.tool_catalog import ToolCatalog
from app.agent.toolbox import RunToolBox
from app.agent.toolkit import Permission, Tool, schema
from app.agent.tools import mcp_listed_tools, run_tools, tools_for
from app.providers.fake_provider import ScriptedResponse
from tests.test_chat import send
from tests.test_delegation import project_conversation

BROWSER = ("browser__browser_navigate", "browser__browser_click", "browser__browser_snapshot")


def stand_in(name: str, permission: Permission = "auto") -> Tool:
    async def handler(_run: AgentRun, _arguments: dict[str, Any]) -> str:
        return f"ran {name}"

    return Tool(name, f"Stand-in {name}.", schema({}, []), permission, handler, scope="always")


def browser_run(registry: RunRegistry, with_browser: bool = True, has_project: bool = True) -> AgentRun:
    browser = {name: stand_in(name) for name in BROWSER} if with_browser else {}
    extra = {**browser, "notes__read": stand_in("notes__read")}
    names = tools_for(has_project, False)
    config = RunConfig(conversation_id=1, environment_id=1, blueprint_layers=[], tool_names=names)
    return registry.start(config, RunServices(extra_tools=extra, catalog=ToolCatalog(browser=frozenset(browser))))


def offered(registry: RunRegistry, run: AgentRun) -> set[str]:
    return {definition.name for definition in RunToolBox(registry, run).definitions}


def test_open_browser_adds_the_browser_tools_from_the_next_model_call_on():
    registry = RunRegistry()
    run = browser_run(registry)
    before = offered(registry, run)
    assert "open_browser" in before and "notes__read" in before
    assert not before & set(BROWSER)
    output, is_error = asyncio.run(call_tool(registry, run, "open_browser", {}))
    assert not is_error and "browser__browser_navigate" in output
    after = offered(registry, run)
    assert after - before == set(BROWSER)
    again, is_error = asyncio.run(call_tool(registry, run, "open_browser", {}))
    assert not is_error and "already available" in again


def test_open_browser_is_not_offered_where_the_browser_is_not_available():
    registry = RunRegistry()
    assert "open_browser" not in offered(registry, browser_run(registry, with_browser=False))
    assert "open_browser" not in offered(registry, browser_run(registry, has_project=False))


def test_a_browser_tool_keeps_its_permission_and_the_gate():
    registry = RunRegistry()
    run = browser_run(registry)
    run.services.extra_tools["browser__browser_click"] = stand_in("browser__browser_click", "ask")
    run.config = replace(run.config, unattended=True)
    asyncio.run(call_tool(registry, run, "open_browser", {}))
    output, is_error = asyncio.run(call_tool(registry, run, "browser__browser_click", {}))
    assert is_error and "ran" not in output  # Nobody approves an unattended run: the asking tool is refused.


def test_a_sub_run_starts_with_what_its_parent_had_joined_and_keeps_its_own():
    registry = RunRegistry()
    parent = browser_run(registry)
    early = registry.child(parent, "worker", None)
    asyncio.run(call_tool(registry, parent, "open_browser", {}))
    late = registry.child(parent, "worker", None)
    assert not offered(registry, early) & set(BROWSER)
    assert offered(registry, late) >= set(BROWSER)
    asyncio.run(call_tool(registry, early, "open_browser", {}))
    assert offered(registry, early) >= set(BROWSER)
    fresh = browser_run(registry)
    child = registry.child(fresh, "worker", None)
    asyncio.run(call_tool(registry, child, "open_browser", {}))
    assert not offered(registry, fresh) & set(BROWSER)  # What a sub-run joins stays in the sub-run.


def test_the_mcp_endpoint_lists_every_tool_at_once_without_open_browser():
    registry = RunRegistry()
    run = browser_run(registry)
    listed = {tool.name for tool in mcp_listed_tools(run)}
    assert set(BROWSER) <= listed and "open_browser" not in listed
    assert {tool.name for tool in run_tools(run)} - listed == {"open_browser"}


def test_a_reply_offers_open_browser_instead_of_the_browser_tools(client, fake):
    from tests.test_browser import with_browser

    with_browser(client)
    _, conversation_id = project_conversation(client)
    fake.responses.append(ScriptedResponse(text="Nothing to check."))
    send(client, conversation_id, "Hello")
    names = {definition.name for definition in fake.requests[0].tool_box.definitions}
    assert "open_browser" in names and not any(name.startswith("browser__") for name in names)


def test_without_the_browser_a_reply_offers_no_open_browser(client, fake):
    client.app.state.browser_command = None
    _, conversation_id = project_conversation(client)
    fake.responses.append(ScriptedResponse(text="Hi."))
    send(client, conversation_id, "Hello")
    names = {definition.name for definition in fake.requests[0].tool_box.definitions}
    assert "open_browser" not in names


@pytest.mark.parametrize(("tools", "offers_browser"), [("[read_file, open_browser]", True), ("[read_file]", False)])
def test_an_agent_with_its_own_tools_gets_the_browser_only_when_it_lists_open_browser(
    client, fake, tools: str, offers_browser: bool
):
    from tests.test_browser import with_browser
    from tests.test_delegation import add_key

    with_browser(client)
    add_key(client)
    content = f"---\ndescription: Looks at pages\ntools: {tools}\n---\n\nLook.\n"
    client.put(
        "/api/v1/blueprint/file", params={"path": "agents/looker.md", "environment_id": 1}, json={"content": content}
    )
    project = client.post("/api/v1/environments/1/projects", json={"name": "Shop"}).json()
    conversation_id = client.post(
        "/api/v1/environments/1/conversations",
        json={"model": "claude-opus-5-5", "effort": "medium", "project_id": project["id"], "agent": "looker"},
    ).json()["id"]
    fake.responses.append(ScriptedResponse(text="Looked."))
    send(client, conversation_id, "Look at the page")
    names = {definition.name for definition in fake.requests[0].tool_box.definitions}
    assert ("open_browser" in names) is offers_browser
    assert not any(name.startswith("browser__") for name in names)
