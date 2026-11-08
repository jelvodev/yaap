"""Coding agents as workers: delegate hands a task to Claude Code or Codex.

The fake CLI below behaves like the real ones as far as YAAP can see: it gets the request's MCP address and token, and
calls YAAP's tools over the MCP endpoint (in-process, through the app itself), never through the in-process tool box.
"""

import json
import sys
import threading
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.providers.base import AgentEvent, AgentRequest, Completed, TextDelta, TokenUsage, ToolCallFinished, ToolCallStarted
from app.providers import claude_code_provider, codex_provider
from app.providers.catalog import OPENAI_MODELS
from app.providers.claude_code_provider import ClaudeCodeProvider
from app.providers.codex_provider import CodexProvider
from app.providers.fake_provider import ScriptedResponse
from app.providers.registry import ProviderSpec
from app.storage.models import AgentRunRecord, UsageRecord
from tests.conftest import parse_events, pass_to_cli
from tests.test_delegation import project_conversation


class FakeCli:
    """A coding agent CLI as YAAP sees it: it lists and calls YAAP's tools over MCP with the token it was given."""

    def __init__(self, app: Any, calls: list[tuple[str, dict]], report: str = "Done by the CLI.") -> None:
        self.app = app
        self.calls = calls
        self.report = report
        self.requests: list[AgentRequest] = []
        self.listed: list[str] = []
        self.results: list[tuple[str, bool]] = []

    def create(self, _api_key: str | None) -> "FakeCliRun":
        return FakeCliRun(self)


class FakeCliRun:
    def __init__(self, cli: FakeCli) -> None:
        self.cli = cli

    async def rpc(self, client: httpx.AsyncClient, request: AgentRequest, method: str, params: dict) -> dict:
        assert request.tool_server is not None
        response = await client.post(
            request.tool_server.url,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            headers={"Authorization": f"Bearer {request.tool_server.token}"},
        )
        assert response.status_code == 200, response.text
        return response.json()["result"]

    async def stream(self, request: AgentRequest) -> AsyncIterator[AgentEvent]:
        self.cli.requests.append(request)
        transport = httpx.ASGITransport(app=self.cli.app)
        async with httpx.AsyncClient(transport=transport, timeout=60) as client:
            await self.rpc(client, request, "initialize", {"protocolVersion": "2025-06-18"})
            tools = await self.rpc(client, request, "tools/list", {})
            self.cli.listed = [tool["name"] for tool in tools["tools"]]
            for index, (name, arguments) in enumerate(self.cli.calls):
                yield ToolCallStarted(f"cli-{index}", name, arguments)
                result = await self.rpc(client, request, "tools/call", {"name": name, "arguments": arguments})
                text = "\n".join(part.get("text", "") for part in result["content"])
                self.cli.results.append((text, result["isError"]))
                yield ToolCallFinished(f"cli-{index}", text, result["isError"])
        yield TextDelta(self.cli.report)
        yield Completed(
            stop_reason="end_turn",
            served_by_model=request.model,
            usage=TokenUsage(input_tokens=500, output_tokens=60),
            billing="subscription",
        )

    async def close(self) -> None:
        return None


def use_fake_cli(client, calls: list[tuple[str, dict]]) -> FakeCli:
    """Replace the Claude Code connection with the fake CLI; conversations keep using the API key connection."""
    cli = FakeCli(client.app, calls)
    registry = client.app.state.providers
    registry.specs = [spec for spec in registry.specs if spec.connection != "claude_code"] + [
        ProviderSpec("anthropic", "Claude Code", None, cli.create, connection="claude_code", runs_agents=True, metered=False)
    ]
    return cli


def delegate_to(fake, worker: str, task: str = "Do the task") -> None:
    fake.responses.append(ScriptedResponse(text="Checked.", tool_calls=(("delegate", {"task": task, "worker": worker}),)))


def records(client) -> tuple[list[AgentRunRecord], list[UsageRecord]]:
    with client.app.state.session_factory() as session:
        runs = list(session.scalars(select(AgentRunRecord).order_by(AgentRunRecord.id)))
        usage = list(session.scalars(select(UsageRecord).order_by(UsageRecord.id)))
        session.expunge_all()
    return runs, usage


def test_a_cli_worker_calls_yaaps_tools_through_the_gate_and_reports(client, fake, tmp_path):
    project, conversation_id = project_conversation(client)
    cli = use_fake_cli(client, [("write_file", {"path": "notes.md", "content": "# Notes\n"}), ("read_file", {"path": "notes.md"})])
    delegate_to(fake, "claude_code", "Write the notes")

    events = parse_events(client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Go"}).text)

    (request,) = cli.requests
    # Only YAAP's tools, over MCP, with a token of its own that is not the parent's.
    assert request.tool_server is not None and request.tool_server.url.endswith("/mcp")
    assert request.messages[0].content[0]["text"] == "Write the notes"
    assert "# Delegated task" in request.system[-1].text
    assert (request.model, request.effort) == ("claude-sonnet-5-5", "medium")
    assert request.working_folder == Path(tmp_path) / "workspace" / project["folder"]
    assert "write_file" in cli.listed and "delegate" not in cli.listed
    assert all(not is_error for _, is_error in cli.results)
    assert (Path(tmp_path) / "workspace" / project["folder"] / "notes.md").read_text() == "# Notes\n"

    result = next(data for name, data in events if name == "tool_result")
    assert "Sub-run by Claude Code on claude-sonnet-5-5: complete." in result["output"]
    assert "Done by the CLI." in result["output"] and "- notes.md" in result["output"]
    progress = [(data["label"], data["step"]) for name, data in events if name == "delegate_progress"]
    assert progress == [("Claude Code", "started"), ("Claude Code", "write_file"), ("Claude Code", "read_file")]

    runs, usage = records(client)
    parent, child = runs
    assert child.parent_run_id == parent.id and child.worker == "claude_code" and child.tool_calls == 2
    # Its usage counts as subscription use: no cost, outside the budget.
    worker_usage = next(record for record in usage if record.purpose == "worker")
    assert (worker_usage.billing, worker_usage.cost, worker_usage.provider) == ("subscription", None, "anthropic")
    listed = client.get("/api/v1/environments/1/runs").json()
    assert listed[0]["worker"] == "claude_code" and listed[0]["parent_run_id"] == listed[1]["id"]


def test_the_cli_workers_approvals_show_in_the_parents_chat_with_its_name(client, fake):
    _, conversation_id = project_conversation(client)
    cli = use_fake_cli(client, [("run_command", {"command": "echo from-the-worker"})])
    delegate_to(fake, "claude_code")
    result: dict[str, Any] = {}

    def send() -> None:
        result["response"] = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Go"})

    thread = threading.Thread(target=send)
    thread.start()
    approval_id = None
    for _ in range(200):
        pending = [(run, key) for run in list(client.app.state.runs.runs.values()) for key in run.state.approvals]
        if pending:
            run, approval_id = pending[0]
            assert run.label == "Claude Code" and run.parent_token is not None
            break
        time.sleep(0.05)
    assert approval_id is not None
    assert client.post(f"/api/v1/approvals/{approval_id}", json={"decision": "approve"}).status_code == 204
    thread.join(timeout=60)

    events = parse_events(result["response"].text)
    asked = next(data for name, data in events if name == "approval_request")
    assert asked["tool"] == "run_command" and asked["summary"].startswith("[Claude Code] ")
    assert any(name == "approval_resolved" and data["decision"] == "approve" for name, data in events)
    assert "from-the-worker" in cli.results[0][0] and not cli.results[0][1]
    child = records(client)[0][1]
    assert child.approvals[0]["decision"] == "approve" and child.approvals[0]["summary"].startswith("[Claude Code]")


def test_a_read_only_parent_gives_a_read_only_cli_worker(client, fake, tmp_path):
    project, conversation_id = project_conversation(client)
    client.patch(f"/api/v1/conversations/{conversation_id}", json={"access": "read_only"})
    cli = use_fake_cli(client, [("write_file", {"path": "x.txt", "content": "x"}), ("list_files", {})])
    delegate_to(fake, "claude_code")

    client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Go"})

    (refused, refused_error), (_, listed_error) = cli.results
    assert refused_error and "read only" in refused
    assert not listed_error
    assert not (Path(tmp_path) / "workspace" / project["folder"] / "x.txt").exists()


def test_the_cli_worker_cannot_reach_tools_the_parent_lacks(client, fake, tmp_path):
    client.put(
        "/api/v1/blueprint/file",
        params={"path": "agents/lead.md", "environment_id": 1},
        json={"content": "---\ndescription: Leads.\ntools: [read_file, list_files, delegate]\n---\nYou lead."},
    )
    project, _ = project_conversation(client)
    conversation_id = client.post(
        "/api/v1/environments/1/conversations",
        json={"model": "claude-opus-5-5", "effort": "medium", "project_id": project["id"], "agent": "lead"},
    ).json()["id"]
    cli = use_fake_cli(client, [("write_file", {"path": "y.txt", "content": "y"}), ("run_command", {"command": "echo hi"})])
    delegate_to(fake, "claude_code")

    client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Go"})

    assert sorted(cli.listed) == ["list_files", "read_file"]
    assert all(is_error and "Unknown tool" in output for output, is_error in cli.results)
    assert not (Path(tmp_path) / "workspace" / project["folder"] / "y.txt").exists()


def test_a_preset_by_name_and_unknown_or_missing_workers(client, fake):
    _, conversation_id = project_conversation(client)
    fake.responses.extend(
        [
            ScriptedResponse(
                text="Done.",
                tool_calls=(
                    ("delegate", {"task": "Deep work", "worker": "deep"}),
                    ("delegate", {"task": "Who?", "worker": "nobody"}),
                    ("delegate", {"task": "Codex?", "worker": "codex"}),
                ),
            ),
            ScriptedResponse(text="Deep done."),
        ]
    )
    events = parse_events(client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Go"}).text)

    deep = fake.requests[1]
    assert (deep.model, deep.effort) == ("claude-sonnet-5-5", "medium")
    results = [data for name, data in events if name == "tool_result"]
    assert "Sub-run by Deep on claude-sonnet-5-5" in results[0]["output"]
    assert results[1]["is_error"] and "No worker named 'nobody'" in results[1]["output"]
    assert "claude_code or codex" in results[1]["output"]
    # The test installation has no Codex connection.
    assert results[2]["is_error"] and "Codex is not available as a worker" in results[2]["output"]
    assert records(client)[0][1].worker == "Deep"


def test_the_cli_worker_settings_take_only_the_vendors_models(client):
    rejected = client.put("/api/v1/settings/claude_code_worker", json={"value": {"model": "claude-opus-5-5", "effort": "huge"}})
    assert rejected.status_code == 422 and "Effort must be one of" in rejected.json()["detail"]
    wrong_vendor = client.put("/api/v1/settings/codex_worker", json={"value": {"model": "claude-opus-5-5", "effort": "low"}})
    assert wrong_vendor.status_code == 422
    saved = client.put(
        "/api/v1/environments/1/settings/claude_code_worker", json={"value": {"model": "claude-haiku-5-5", "effort": "low"}}
    )
    assert saved.status_code == 200 and saved.json()["source"] == "environment"
    defaults = {item["key"]: item["value"] for item in client.get("/api/v1/settings").json()}
    assert defaults["claude_code_worker"] == {"model": "claude-sonnet-5-5", "effort": "medium"}
    assert defaults["codex_worker"] == {"model": "gpt-6.1-sol", "effort": "medium"}


def test_the_workers_list_shows_presets_next_to_the_clis(client):
    workers = client.get("/api/v1/environments/1/workers").json()
    by_name = {worker["name"]: worker for worker in workers}
    assert [worker["kind"] for worker in workers] == ["preset"] * 4 + ["cli", "cli"]
    balanced = by_name["Balanced"]
    assert (balanced["model"], balanced["effort"], balanced["is_default"]) == ("claude-sonnet-5-5", "low", True)
    assert balanced["billing"] == "metered" and balanced["connection"] == "Anthropic"
    # Without a key the API presets cannot work; the subscription CLI needs none.
    assert not balanced["available"] and "API key" in balanced["unavailable_reason"]
    # A preset without a worker model works on its main model.
    assert (by_name["Fast"]["model"], by_name["Fast"]["effort"]) == ("claude-sonnet-5-5", "low")
    claude = by_name["claude_code"]
    assert claude["label"] == "Claude Code" and claude["billing"] == "subscription" and claude["available"]
    assert (claude["model"], claude["model_name"]) == ("claude-sonnet-5-5", "Claude Sonnet 5.5")
    assert not by_name["codex"]["available"] and "not available in this installation" in by_name["codex"]["unavailable_reason"]

    client.put("/api/v1/settings/anthropic_connection", json={"value": "claude_code"})
    client.put("/api/v1/environments/1/settings/claude_code_worker", json={"value": {"model": "claude-opus-5-5", "effort": "high"}})
    workers = {worker["name"]: worker for worker in client.get("/api/v1/environments/1/workers").json()}
    assert workers["Balanced"]["available"] and workers["Balanced"]["billing"] == "subscription"
    assert (workers["claude_code"]["model"], workers["claude_code"]["effort"]) == ("claude-opus-5-5", "high")


# --- The real adapters, started as workers with a fake binary ------------------------------------------------------

FAKE_BINARY = """
import json, os, sys
argv = sys.argv[1:]
log = {"argv": argv, "cwd": os.getcwd(), "stdin": sys.stdin.read(),
       "token": os.environ.get("YAAP_TOOL_TOKEN"), "max_output": os.environ.get("CLAUDE_CODE_MAX_OUTPUT_TOKENS")}
if "--mcp-config" in argv:
    log["mcp"] = json.load(open(argv[argv.index("--mcp-config") + 1], encoding="utf-8"))
with open(os.environ["FAKE_CLI_LOG"], "a", encoding="utf-8") as file:
    file.write(json.dumps(log) + "\\n")
with open(os.environ["FAKE_CLI_OUTPUT"], encoding="utf-8") as file:
    sys.stdout.write(file.read())
"""

CLAUDE_LINES = [
    {"type": "system", "subtype": "init", "session_id": "worker-session"},
    {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Report from Claude Code."}}},
    {
        "type": "result",
        "is_error": False,
        "result": "Report from Claude Code.",
        "session_id": "worker-session",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 40, "output_tokens": 9},
        "modelUsage": {"claude-haiku-5-5": {}},
    },
]

CODEX_LINES = [
    {"type": "thread.started", "thread_id": "worker-thread"},
    {"type": "item.completed", "item": {"id": "m1", "type": "agent_message", "text": "Report from Codex."}},
    {"type": "turn.completed", "usage": {"input_tokens": 70, "cached_input_tokens": 20, "output_tokens": 11}},
]


@pytest.fixture
def fake_binary(tmp_path, monkeypatch):
    script = tmp_path / "fake_cli.py"
    script.write_text(FAKE_BINARY, encoding="utf-8")
    log, output = tmp_path / "cli-log.jsonl", tmp_path / "cli-output.jsonl"
    monkeypatch.setenv("FAKE_CLI_LOG", str(log))
    monkeypatch.setenv("FAKE_CLI_OUTPUT", str(output))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    for module in (claude_code_provider, codex_provider):
        pass_to_cli(monkeypatch, module, "FAKE_CLI_LOG", "FAKE_CLI_OUTPUT")
    monkeypatch.setattr(ClaudeCodeProvider, "thinking_display_supported", True)

    class Binary:
        command = [sys.executable, str(script)]

        def output(self, lines: list[dict]) -> None:
            output.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")

        def log(self) -> dict:
            return json.loads(log.read_text(encoding="utf-8").splitlines()[-1])

    return Binary()


def sub_run_tokens(client) -> list[str]:
    """Record the token of every run started from now on."""
    registry = client.app.state.runs
    tokens: list[str] = []
    add = registry.add  # Both a run and a sub-run (RunRegistry.child) are registered through add.

    def recording_add(run):
        tokens.append(run.token)
        return add(run)

    registry.add = recording_add
    return tokens


def test_claude_code_starts_as_worker_with_only_yaaps_tools_in_the_project_folder(client, fake, fake_binary, tmp_path):
    project, conversation_id = project_conversation(client)
    registry = client.app.state.providers
    registry.specs = [spec for spec in registry.specs if spec.connection != "claude_code"] + [
        ProviderSpec(
            "anthropic", "Claude Code", None, lambda _key: ClaudeCodeProvider(fake_binary.command),
            connection="claude_code", runs_agents=True, metered=False,
        )
    ]  # fmt: skip
    client.put("/api/v1/environments/1/settings/claude_code_worker", json={"value": {"model": "claude-haiku-5-5", "effort": "low"}})
    fake_binary.output(CLAUDE_LINES)
    tokens = sub_run_tokens(client)
    delegate_to(fake, "claude_code", "Tidy the readme")

    events = parse_events(client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Go"}).text)

    log = fake_binary.log()
    arguments = log["argv"]
    assert arguments[arguments.index("--model") + 1] == "claude-haiku-5-5"
    assert arguments[arguments.index("--effort") + 1] == "low"
    assert arguments[arguments.index("--tools") + 1] == "" and "--strict-mcp-config" in arguments
    assert arguments[arguments.index("--max-turns") + 1] == "40"
    assert Path(log["cwd"]) == Path(tmp_path) / "workspace" / project["folder"]
    parent_token, child_token = tokens
    (server,) = log["mcp"]["mcpServers"].values()
    assert server["url"].endswith("/mcp") and server["headers"]["Authorization"] == f"Bearer {child_token}"
    assert parent_token not in json.dumps(log)
    assert json.loads(log["stdin"])["message"]["content"] == [{"type": "text", "text": "Tidy the readme"}]

    result = next(data for name, data in events if name == "tool_result")
    assert "Sub-run by Claude Code on claude-haiku-5-5: complete." in result["output"]
    assert "Report from Claude Code." in result["output"]
    worker_usage = next(record for record in records(client)[1] if record.purpose == "worker")
    assert (worker_usage.billing, worker_usage.input_tokens) == ("subscription", 40)


def test_codex_starts_as_worker_with_the_token_in_its_environment(client, fake, fake_binary):
    _, conversation_id = project_conversation(client)
    registry = client.app.state.providers
    registry.fixed_models = [*registry.fixed_models, *OPENAI_MODELS]
    registry.specs = [
        *registry.specs,
        ProviderSpec(
            "openai", "Codex", None, lambda _key: CodexProvider(fake_binary.command),
            connection="codex", runs_agents=True, metered=False,
        ),
    ]  # fmt: skip
    fake_binary.output(CODEX_LINES)
    tokens = sub_run_tokens(client)
    delegate_to(fake, "codex", "Fix the tests")

    events = parse_events(client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Go"}).text)

    log = fake_binary.log()
    arguments = log["argv"]
    assert arguments[arguments.index("--model") + 1] == "gpt-6.1-sol"
    assert 'model_reasoning_effort="medium"' in arguments and "shell_tool" in arguments
    assert any(argument.startswith("mcp_servers.yaap.url=") and argument.endswith('/mcp"') for argument in arguments)
    assert log["token"] == tokens[1] and tokens[0] not in json.dumps(log)
    assert log["stdin"] == "Fix the tests"

    result = next(data for name, data in events if name == "tool_result")
    assert "Sub-run by Codex on gpt-6.1-sol: complete." in result["output"] and "Report from Codex." in result["output"]
    runs, usage = records(client)
    assert runs[1].worker == "codex"
    worker_usage = next(record for record in usage if record.purpose == "worker")
    assert (worker_usage.provider, worker_usage.billing, worker_usage.cost) == ("openai", "subscription", None)
