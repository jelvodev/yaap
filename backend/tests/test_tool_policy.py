import asyncio
from pathlib import Path

from app.agent.policy import ToolPolicy, command_allowed, url_allowed
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
