"""Outside MCP tools behind a search (efficiency): the server list in the prompt, find_tools, and the
per-server setting list_all_tools."""

import asyncio
import sys
from typing import Any

import pytest

from app.agent.gate import call_tool
from app.agent.runs import AgentRun, RunConfig, RunRegistry, RunServices
from app.agent.tool_catalog import (
    SMALL_SERVER_TOOLS,
    ServerListing,
    ToolCatalog,
    best_matches,
    listing_part,
    lists_all,
    with_listing,
)
from app.agent.toolbox import RunToolBox
from app.agent.toolkit import Tool, schema
from app.agent.tools import mcp_listed_tools, tools_for
from app.mcp_client.config import ServerConfig, parse_servers, validate_servers
from app.mcp_client.manager import McpTool
from app.mcp_client.runs import server_listings
from app.providers.base import SystemPart
from app.providers.fake_provider import ScriptedResponse
from tests.test_chat import send
from tests.test_delegation import project_conversation
from tests.test_mcp_client import SERVER

GITHUB = {
    "get_issue": "Get details of a specific issue in a GitHub repository.",
    "list_issues": "List issues in a GitHub repository with filtering options.",
    "create_issue": "Create a new issue in a GitHub repository.",
    "update_issue": "Update an existing issue: title, body, state or labels.",
    "add_issue_comment": "Add a comment to a specific issue.",
    "search_issues": "Search for issues and pull requests across repositories.",
    "get_pull_request": "Get details of a specific pull request.",
    "list_pull_requests": "List pull requests in a repository.",
    "create_pull_request": "Create a new pull request in a GitHub repository.",
    "merge_pull_request": "Merge a pull request.",
    "get_pull_request_files": "Get the files changed in a pull request.",
    "create_pull_request_review": "Create a review on a pull request.",
    "get_file_contents": "Get the contents of a file or directory from a repository.",
    "create_or_update_file": "Create or update a single file in a repository.",
    "push_files": "Push multiple files in a single commit.",
    "list_commits": "Get the list of commits of a branch.",
    "create_branch": "Create a new branch in a repository.",
    "search_code": "Search for code across GitHub repositories.",
    "search_repositories": "Search for GitHub repositories.",
    "list_workflow_runs": "List the GitHub Actions workflow runs of a repository.",
}
NOTES = {"read_note": "Read a note.", "write_note": "Write a note."}


def fixture_tool(name: str, description: str) -> Tool:
    async def handler(_run: AgentRun, _arguments: dict[str, Any]) -> str:
        return f"ran {name}"

    server = name.split("__")[0]
    return Tool(name, f"[MCP server {server}] {description}", schema({}, []), "auto", handler, scope="always")


def search_run(registry: RunRegistry, github_listed: bool = False) -> AgentRun:
    github = {f"github__{name}": fixture_tool(f"github__{name}", text) for name, text in GITHUB.items()}
    notes = {f"notes__{name}": fixture_tool(f"notes__{name}", text) for name, text in NOTES.items()}
    catalog = ToolCatalog(
        servers=(
            ServerListing("github", "Issues and pull requests.", tuple(github), github_listed),
            ServerListing("notes", "", tuple(notes), True),
        )
    )
    config = RunConfig(conversation_id=1, environment_id=1, blueprint_layers=[], tool_names=tools_for(False, False))
    return registry.start(config, RunServices(extra_tools={**github, **notes}, catalog=catalog))


def offered(registry: RunRegistry, run: AgentRun) -> set[str]:
    return {definition.name for definition in RunToolBox(registry, run).definitions}


def test_find_tools_returns_the_matches_and_adds_them_to_the_run():
    registry = RunRegistry()
    run = search_run(registry)
    before = offered(registry, run)
    assert "find_tools" in before and {"notes__read_note", "notes__write_note"} <= before
    assert not any(name.startswith("github__") for name in before)
    output, is_error = asyncio.run(call_tool(registry, run, "find_tools", {"query": "create an issue"}))
    assert not is_error
    assert output.splitlines()[1] == "- github__create_issue: Create a new issue in a GitHub repository."
    added = offered(registry, run) - before
    assert "github__create_issue" in added and added <= {f"github__{name}" for name in GITHUB}
    assert "[MCP server" not in output


def test_find_tools_narrows_to_a_server_and_says_when_nothing_matches():
    registry = RunRegistry()
    run = search_run(registry)
    nothing, is_error = asyncio.run(call_tool(registry, run, "find_tools", {"query": "weather", "server": "github"}))
    assert not is_error and "No tools of github matched" in nothing
    unknown, is_error = asyncio.run(call_tool(registry, run, "find_tools", {"query": "issue", "server": "jira"}))
    assert is_error and "No MCP server jira" in unknown and "github" in unknown
    listed, is_error = asyncio.run(call_tool(registry, run, "find_tools", {"query": "note", "server": "notes"}))
    assert not is_error and "offered already" in listed


@pytest.mark.parametrize(
    ("query", "first"),
    [
        ("create issue", "github__create_issue"),
        ("merge a pull request", "github__merge_pull_request"),
        ("workflow runs", "github__list_workflow_runs"),
        ("comment on an issue", "github__add_issue_comment"),
        ("search code", "github__search_code"),
        ("commits", "github__list_commits"),
        ("Actions", "github__list_workflow_runs"),
    ],
)
def test_the_score_puts_the_tool_whose_name_matches_first(query, first):
    candidates = {f"github__{name}": text for name, text in GITHUB.items()}
    assert best_matches(query, candidates)[0] == first


def test_no_match_without_a_shared_word():
    assert best_matches("weather forecast", {f"github__{name}": text for name, text in GITHUB.items()}) == []


def test_a_server_with_all_tools_listed_offers_them_without_find_tools():
    registry = RunRegistry()
    run = search_run(registry, github_listed=True)
    names = offered(registry, run)
    assert "find_tools" not in names and "github__create_issue" in names


@pytest.mark.parametrize(
    ("setting", "count", "listed"),
    [
        (None, 1, True),
        (None, SMALL_SERVER_TOOLS, True),
        (None, SMALL_SERVER_TOOLS + 1, False),
        (None, 20, False),
        (True, 20, True),
        (False, 2, False),
    ],
)
def test_a_small_server_stays_fully_listed_unless_its_setting_says_otherwise(setting, count, listed):
    assert lists_all(setting, count) is listed
    server = ServerConfig(name="s", transport="http", url="https://example.com", list_all_tools=setting)
    tools = [McpTool(f"s__t{index}", "s", f"t{index}", "", {}, False, False, "k") for index in range(count)]
    other = McpTool("x__y", "x", "y", "", {}, False, False, "k")
    (listing,) = server_listings([server], [*tools, other])
    assert listing.listed is listed and listing.tools == tuple(tool.name for tool in tools)


def test_the_setting_is_checked_and_read():
    base = {"transport": "http", "url": "https://example.com/mcp"}
    assert validate_servers({"s": {**base, "list_all_tools": False}}) is None
    assert "list_all_tools is true or false" in validate_servers({"s": {**base, "list_all_tools": "yes"}})
    assert parse_servers({"s": {**base, "list_all_tools": True}})[0].list_all_tools is True
    assert parse_servers({"s": base})[0].list_all_tools is None


def test_the_server_list_names_each_server_and_the_tools_behind_find_tools():
    registry = RunRegistry()
    part = listing_part(search_run(registry).catalog)
    assert part is not None and part.cacheable
    lines = part.text.splitlines()
    assert lines[0] == "## MCP servers"
    github = next(line for line in lines if line.startswith("- github"))
    assert github.startswith("- github (20 tools, find_tools): Issues and pull requests. Tools: get_issue, list_issues")
    assert "- notes (2 tools)" in lines
    assert listing_part(ToolCatalog()) is None


def test_the_server_list_follows_the_last_cacheable_part():
    catalog = ToolCatalog(servers=(ServerListing("notes", "", ("notes__read_note",), True),))
    system = [SystemPart("base"), SystemPart("project", cacheable=True), SystemPart("document")]
    placed = with_listing(system, catalog)
    assert [part.text for part in placed][:2] == ["base", "project"]
    assert placed[2].text.startswith("## MCP servers") and placed[3].text == "document"
    assert with_listing(system, ToolCatalog()) == system


def test_the_mcp_endpoint_lists_every_tool_without_find_tools():
    registry = RunRegistry()
    listed = {tool.name for tool in mcp_listed_tools(search_run(registry))}
    assert "github__create_issue" in listed and "find_tools" not in listed


def test_the_server_list_appears_in_the_prompt_of_a_reply_and_a_small_server_is_listed(client, fake):
    _, conversation_id = project_conversation(client)
    servers = {"test": {"transport": "stdio", "command": sys.executable, "args": [SERVER], "description": "Echoes."}}
    assert client.put("/api/v1/environments/1/settings/mcp_servers", json={"value": servers}).status_code == 200
    fake.responses.append(ScriptedResponse(text="Hi."))
    send(client, conversation_id, "Hello")
    request = fake.requests[0]
    listing = next(part for part in request.system if part.text.startswith("## MCP servers"))
    assert "- test (5 tools): Echoes." in listing.text and listing.cacheable
    names = {definition.name for definition in request.tool_box.definitions}
    assert "test__echo" in names and "find_tools" not in names


def test_a_server_set_to_search_offers_find_tools_in_a_reply(client, fake):
    _, conversation_id = project_conversation(client)
    servers = {"test": {"transport": "stdio", "command": sys.executable, "args": [SERVER], "list_all_tools": False}}
    assert client.put("/api/v1/environments/1/settings/mcp_servers", json={"value": servers}).status_code == 200
    fake.responses.append(
        ScriptedResponse(text="Found it.", tool_calls=(("find_tools", {"query": "environment variable"}),))
    )
    events = send(client, conversation_id, "Which tool reads the environment?")
    result = next(data for name, data in events if name == "tool_result")
    assert "test__env" in result["output"] and not result["is_error"]
    names = {definition.name for definition in fake.requests[0].tool_box.definitions}
    assert "find_tools" in names and "test__env" in names and "test__echo" not in names
