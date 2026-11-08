import asyncio
from pathlib import Path

from app.agent.runs import RunConfig, RunRegistry
from app.agent.gate import call_tool
from app.agent.tools import tools_for
from app.git.service import run_git
from app.projects.memory import TEMPLATE, add_entry
from app.providers.fake_provider import ScriptedResponse


def test_entries_land_at_the_end_of_their_section():
    text = add_entry(TEMPLATE, "Decisions", "Use SQLite.")
    text = add_entry(text, "Decisions", "No ORM magic.")
    text = add_entry(text, "Notes", "Ask before deploying.")
    assert text == (
        "# Project memory\n\n"
        "## Decisions\n\n- Use SQLite.\n- No ORM magic.\n\n"
        "## Conventions\n\n"
        "## Lessons learned\n\n"
        "## Notes\n\n- Ask before deploying.\n"
    )
    assert add_entry("# Mine\n", "Decisions", "x") == "# Mine\n\n## Decisions\n\n- x\n"


def test_the_agent_remembers_through_a_commit_of_only_the_memory(client):
    project = client.post("/api/v1/environments/1/projects", json={"name": "Site"}).json()
    folder = Path(client.app.state.workspace_dir) / project["folder"]
    (folder / "draft.txt").write_text("not mine to commit\n", encoding="utf-8")
    registry = RunRegistry()
    run = registry.start(RunConfig(
        conversation_id=1,
        environment_id=1,
        blueprint_layers=[],
        tool_names=tools_for(has_project=True, has_attachments=False),
        project_id=project["id"],
        project_folder=folder,
    ))

    output, error = asyncio.run(
        call_tool(registry, run, "update_memory", {"section": "Conventions", "entry": "Dates are ISO 8601."})
    )

    assert not error, output
    memory = client.get(f"/api/v1/projects/{project['id']}/memory").json()
    assert memory["exists"] and "## Conventions\n\n- Dates are ISO 8601." in memory["content"]
    log = run_git(["log", "--name-only", "--format=%s"], folder).stdout
    assert "Remember: Dates are ISO 8601." in log and ".blueprint/memory.md" in log
    assert "draft.txt" not in log
    bad, error = asyncio.run(call_tool(registry, run, "update_memory", {"section": "Gossip", "entry": "x"}))
    assert error and "Section must be one of" in bad


def test_memory_is_part_of_every_project_conversation(client, fake):
    client.put("/api/v1/settings/anthropic_connection", json={"value": "claude_code"})
    project = client.post("/api/v1/environments/1/projects", json={"name": "Site"}).json()
    empty = client.get(f"/api/v1/projects/{project['id']}/memory").json()
    assert empty == {"content": TEMPLATE, "exists": False}
    saved = client.put(
        f"/api/v1/projects/{project['id']}/memory",
        json={"content": TEMPLATE.replace("## Decisions\n\n", "## Decisions\n\n- Deploy on Fridays never.\n\n")},
    )
    assert saved.status_code == 200
    conversation_id = client.post(
        "/api/v1/environments/1/conversations",
        json={"model": "claude-opus-5-5", "effort": "medium", "project_id": project["id"]},
    ).json()["id"]
    fake.responses.append(ScriptedResponse(text="Noted."))

    client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Wat weet je?"})

    system = "\n\n".join(part.text for part in fake.requests[0].system)
    assert "## Project memory" in system and "- Deploy on Fridays never." in system


def test_project_defaults_are_checked(client):
    project = client.post("/api/v1/environments/1/projects", json={"name": "Site"}).json()
    url = f"/api/v1/projects/{project['id']}"
    assert client.patch(url, json={"default_agent": "ghost"}).status_code == 422
    assert client.patch(url, json={"default_preset": "Nope"}).status_code == 422
    updated = client.patch(url, json={"default_preset": "deep"}).json()
    assert updated["default_preset"] == "Deep" and updated["name"] == "Site"
    cleared = client.patch(url, json={"default_preset": None, "name": "Website"}).json()
    assert cleared["default_preset"] is None and cleared["name"] == "Website"
