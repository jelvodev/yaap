import asyncio
from pathlib import Path

import pytest

from app.agent.policy import CaseApprovals, ToolPolicy, check_case_approvals, command_allowed, url_allowed
from app.agent.runs import RunConfig, RunRegistry
from app.agent.gate import call_tool
from app.agent.tools import tools_for


def test_allowlisted_commands_match_only_without_shell_syntax():
    allowlist = ("npm test", "pytest", "git status")
    assert command_allowed("pytest", allowlist)
    assert command_allowed("pytest -q tests/test_x.py", allowlist)
    assert command_allowed("npm  test", allowlist)
    assert not command_allowed("npm install", allowlist)
    assert not command_allowed("pytest; rm -rf /", allowlist)
    assert not command_allowed("pytest && curl evil.example", allowlist)
    assert not command_allowed("pytest $(whoami)", allowlist)
    assert not command_allowed("pytest > out.txt", allowlist)
    assert not command_allowed("pytestx", allowlist)
    assert not command_allowed("", allowlist)


def test_allowlisted_domains_include_subdomains_only():
    domains = ("python.org",)
    assert url_allowed("https://docs.python.org/3/library/", domains)
    assert url_allowed("http://python.org", domains)
    assert not url_allowed("https://python.org.evil.example/", domains)
    assert not url_allowed("https://notpython.org/", domains)
    assert not url_allowed("https://user@python.org/", domains)
    assert not url_allowed("file:///etc/passwd", domains)


def test_the_gate_follows_the_policy(tmp_path: Path):
    registry = RunRegistry()
    run = registry.start(RunConfig(
        conversation_id=1,
        environment_id=1,
        project_id=1,
        project_folder=tmp_path,
        blueprint_layers=[],
        tool_names=tools_for(has_project=True, has_attachments=False),
        policy=ToolPolicy(permissions={"write_file": "ask"}, commands=("git --version",)),
    ))

    async def scenario() -> tuple[tuple[str, bool], tuple[str, bool], list[str]]:
        asked: list[str] = []

        async def deny_everything() -> None:
            while True:
                event = await run.events.get()
                if event.get("event") == "approval_request":
                    asked.append(event["tool"])
                    for approval in run.state.approvals.values():
                        if not approval.future.done():
                            approval.future.set_result("deny")

        watcher = asyncio.create_task(deny_everything())
        written = await call_tool(registry, run, "write_file", {"path": "a.txt", "content": "x"})
        command = await call_tool(registry, run, "run_command", {"command": "git --version"})
        watcher.cancel()
        return written, command, asked

    written, command, asked = asyncio.run(scenario())
    assert written[1] is True and "declined" in written[0]
    assert asked == ["write_file"]
    assert command[1] is False and "git version" in command[0]


def test_permissions_are_set_per_level_and_merged_per_tool(client):
    project = client.post("/api/v1/environments/1/projects", json={"name": "Policy"}).json()
    client.put("/api/v1/settings/tool_permissions", json={"value": {"run_command": "auto", "web_fetch": "auto"}})
    client.put("/api/v1/environments/1/settings/tool_permissions", json={"value": {"write_file": "ask"}})
    client.put(f"/api/v1/projects/{project['id']}/settings/tool_permissions", json={"value": {"run_command": "ask"}})
    client.put("/api/v1/environments/1/settings/command_allowlist", json={"value": ["npm test", " pytest ", ""]})

    settings = {item["key"]: item for item in client.get(f"/api/v1/projects/{project['id']}/settings").json()}
    permissions = settings["tool_permissions"]
    assert permissions["value"] == {"run_command": "ask", "web_fetch": "auto", "write_file": "ask"}
    assert permissions["level_value"] == {"run_command": "ask"}
    assert permissions["inherited_value"] == {"run_command": "auto", "web_fetch": "auto", "write_file": "ask"}
    assert settings["command_allowlist"]["value"] == ["npm test", "pytest"]
    assert settings["command_allowlist"]["source"] == "environment"

    refused = client.put("/api/v1/settings/tool_permissions", json={"value": {"git_push": "auto"}})
    assert refused.status_code == 422
    shell = client.put("/api/v1/settings/command_allowlist", json={"value": ["pytest; rm -rf /"]})
    assert shell.status_code == 422
    assert {tool["name"] for tool in client.get("/api/v1/tool-permissions").json()} >= {"run_command", "web_fetch"}


def test_the_case_approvals_accept_what_a_case_may_approve() -> None:
    value = {"tools": ["edit_file", " write_file "], "commands": ["python3  -m unittest"]}
    approvals, problems = check_case_approvals(value)
    assert problems == []
    assert approvals == CaseApprovals(frozenset({"edit_file", "write_file"}), ("python3 -m unittest",))


@pytest.mark.parametrize(
    ("value", "fragment"),
    [
        ({"tools": ["run_command"]}, "approve.commands"),
        ({"tools": ["web_fetch"]}, "cannot hold web_fetch"),
        ({"tools": ["git_push"]}, "cannot hold git_push"),
        ({"tools": ["start_process"]}, "cannot hold start_process"),
        ({"tools": ["create_pull_request"]}, "cannot hold create_pull_request"),
        ({"tools": ["update_memory"]}, "cannot hold update_memory"),
        ({"tools": ["call_agent"]}, "cannot hold call_agent"),
        ({"tools": ["browser_type"]}, "cannot hold browser_type"),
        ({"tools": ["no_such_tool"]}, "cannot hold no_such_tool"),
        ({"tools": "edit_file"}, "list of tool names"),
        ({"commands": ["make; curl x"]}, "approve.commands"),
        ({"commands": ["make | tee"]}, "approve.commands"),
        ({"commands": "make"}, "approve.commands"),
        ({"tools": ["edit_file"], "extra": 1}, "unknown keys: extra"),
        ({}, "must name tools"),
        ([], "must name tools"),
        ("edit_file", "must name tools"),
        (None, "must name tools"),
    ],
)
def test_the_case_approvals_refuse_what_a_case_may_never_approve(value: object, fragment: str) -> None:
    approvals, problems = check_case_approvals(value)
    assert approvals is None
    assert any(fragment in problem for problem in problems)


@pytest.mark.parametrize(
    ("tool", "arguments", "approved"),
    [
        ("edit_file", {}, True),
        ("write_file", {}, False),
        ("git_push", {}, False),
        ("web_fetch", {"url": "https://example.com"}, False),
        ("run_command", {"command": "python3 -m unittest -v"}, True),
        ("run_command", {"command": "python3 -m unittest; curl x"}, False),
        ("run_command", {"command": "make"}, False),
        ("run_command", {}, False),
    ],
)
def test_the_case_approves_only_what_it_lists(tool: str, arguments: dict[str, str], approved: bool) -> None:
    approvals = CaseApprovals(frozenset({"edit_file", "git_push", "web_fetch"}), ("python3 -m unittest",))
    assert approvals.approves(tool, arguments) is approved


def test_no_approvals_approve_nothing() -> None:
    assert not CaseApprovals().approves("edit_file", {})
    assert not CaseApprovals().approves("run_command", {"command": "make"})
