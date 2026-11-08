import asyncio
import sys
import threading
import time
from pathlib import Path

import pytest

from app.agent.runs import RunConfig, RunRegistry
from app.agent.gate import call_tool
from app.agent.tools import tools_for
from app.agent.web_tools import html_to_text


@pytest.fixture
def project(tmp_path):
    folder = tmp_path / "project"
    folder.mkdir()
    (folder / "app.py").write_text("print('hello')\n", encoding="utf-8")
    (folder / "src").mkdir()
    (folder / "src" / "util.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    return folder


def start_run(registry: RunRegistry, folder: Path):
    return registry.start(RunConfig(
        conversation_id=1,
        environment_id=1,
        project_id=1,
        project_folder=folder,
        blueprint_layers=[],
        tool_names=tools_for(has_project=True, has_attachments=False),
        git_author=None,
        github_token=None,
    ))


def call(registry, run, name, arguments):
    return asyncio.run(call_tool(registry, run, name, arguments))


def test_file_tools_work_inside_the_project(project):
    registry = RunRegistry()
    run = start_run(registry, project)

    listing, _ = call(registry, run, "list_files", {"recursive": True})
    assert listing.splitlines() == ["app.py", "src/", "src/util.py"]
    content, _ = call(registry, run, "read_file", {"path": "src/util.py"})
    assert "     2\t    return a + b" in content
    found, _ = call(registry, run, "search", {"pattern": r"def \w+", "glob": "*.py"})
    assert found == "src/util.py:1: def add(a, b):"

    created, error = call(registry, run, "write_file", {"path": "docs/readme.md", "content": "# Hi\n"})
    assert not error and (project / "docs" / "readme.md").read_text() == "# Hi\n"
    edited, error = call(registry, run, "edit_file", {"path": "app.py", "old_string": "hello", "new_string": "bye"})
    assert not error and "print('bye')" in (project / "app.py").read_text()


def test_paths_outside_the_project_are_refused(project, tmp_path):
    (tmp_path / "secret.txt").write_text("nope")
    registry = RunRegistry()
    run = start_run(registry, project)
    for name, arguments in (
        ("read_file", {"path": "../secret.txt"}),
        ("write_file", {"path": "../evil.txt", "content": "x"}),
        ("write_file", {"path": ".git/config", "content": "x"}),
        ("list_files", {"path": "/"}),
    ):
        output, is_error = call(registry, run, name, arguments)
        assert is_error, (name, arguments, output)
    assert not (tmp_path / "evil.txt").exists()


def test_edit_needs_a_unique_match(project):
    registry = RunRegistry()
    run = start_run(registry, project)
    (project / "twice.txt").write_text("a\na\n")
    output, is_error = call(registry, run, "edit_file", {"path": "twice.txt", "old_string": "a", "new_string": "b"})
    assert is_error and "occurs 2 times" in output


def test_command_waits_for_approval_and_hides_secrets(project, monkeypatch):
    monkeypatch.setenv("YAAP_MASTER_KEY", "top-secret")
    registry = RunRegistry()
    run = start_run(registry, project)
    command = f'"{sys.executable}" -c "import os; print(os.environ.get(\'YAAP_MASTER_KEY\', \'hidden\'))"'

    async def approve_when_asked():
        event = await run.events.get()
        assert event["event"] == "approval_request" and event["tool"] == "run_command"
        registry.decide(event["approval_id"], "approve")

    async def scenario():
        approver = asyncio.create_task(approve_when_asked())
        result = await call_tool(registry, run, "run_command", {"command": command})
        await approver
        return result

    output, is_error = asyncio.run(scenario())
    assert not is_error
    assert output.startswith("Exit code 0") and "hidden" in output and "top-secret" not in output


def test_declined_action_is_not_run(project):
    registry = RunRegistry()
    run = start_run(registry, project)

    async def scenario():
        async def deny():
            event = await run.events.get()
            registry.decide(event["approval_id"], "deny")

        task = asyncio.create_task(deny())
        result = await call_tool(registry, run, "run_command", {"command": "echo should-not-run > ran.txt"})
        await task
        return result

    output, is_error = asyncio.run(scenario())
    assert is_error and "declined" in output
    assert not (project / "ran.txt").exists()


def test_finishing_a_run_denies_open_approvals(project):
    registry = RunRegistry()
    run = start_run(registry, project)

    async def scenario():
        async def finish():
            await run.events.get()
            registry.finish(run)

        task = asyncio.create_task(finish())
        result = await call_tool(registry, run, "git_push", {})
        await task
        return result

    output, is_error = asyncio.run(scenario())
    assert is_error and "declined" in output


def test_html_is_reduced_to_text():
    page = "<html><head><style>x{}</style><script>alert(1)</script></head><body><h1>Title</h1><p>A &amp; B</p></body></html>"
    assert html_to_text(page) == "Title\n A & B"


def mcp(client, token: str | None, body: dict, origin: str | None = None):
    headers = {"Accept": "application/json, text/event-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if origin:
        headers["Origin"] = origin
    return client.post("/mcp", json=body, headers=headers)


def test_mcp_endpoint_needs_a_run_token(client, project):
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    assert mcp(client, None, request).status_code == 401
    assert mcp(client, "made-up", request).status_code == 401
    run = start_run(client.app.state.runs, project)
    assert mcp(client, run.token, request, origin="https://evil.example").status_code == 403
    # Without a token GET is refused like every route; with one, it is not part of the transport.
    assert client.get("/mcp").status_code == 401
    assert client.get("/mcp", headers={"Authorization": f"Bearer {run.token}"}).status_code == 405


def test_mcp_initialize_list_and_call(client, project):
    run = start_run(client.app.state.runs, project)
    initialized = mcp(
        client, run.token, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}
    ).json()
    assert initialized["result"]["protocolVersion"] == "2025-06-18"
    assert initialized["result"]["serverInfo"]["name"] == "yaap"
    assert mcp(client, run.token, {"jsonrpc": "2.0", "method": "notifications/initialized"}).status_code == 202

    tools = mcp(client, run.token, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).json()["result"]["tools"]
    assert {"read_file", "edit_file", "run_command", "git_push", "load_standard"} <= {tool["name"] for tool in tools}

    called = mcp(
        client,
        run.token,
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "read_file", "arguments": {"path": "app.py"}}},
    ).json()["result"]
    assert called["isError"] is False and "print('hello')" in called["content"][0]["text"]


def test_approval_through_the_api(client, project):
    registry = client.app.state.runs
    run = start_run(registry, project)
    result = {}

    def call_in_background():
        result["response"] = mcp(
            client,
            run.token,
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {"name": "run_command", "arguments": {"command": "echo approved"}},
            },
        )

    thread = threading.Thread(target=call_in_background)
    thread.start()
    for _ in range(100):
        if run.state.approvals:
            break
        time.sleep(0.05)
    approval_id = next(iter(run.state.approvals))
    assert client.post(f"/api/v1/approvals/{approval_id}", json={"decision": "approve"}).status_code == 204
    thread.join(timeout=30)
    output = result["response"].json()["result"]["content"][0]["text"]
    assert "approved" in output
    assert client.post(f"/api/v1/approvals/{approval_id}", json={"decision": "approve"}).status_code == 404
