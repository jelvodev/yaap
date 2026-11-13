"""The agent's update_current_state tool."""

import asyncio
import subprocess
from pathlib import Path

import pytest

from app.agent.gate import call_tool
from app.agent.runs import RunConfig, RunRegistry
from app.agent.state_tools import STATE_TOOLS
from app.agent.tools import TOOLS_BY_NAME, tools_for
from app.projects import state_refresh
from app.projects.current_state import COMMIT_PREFIX, MAX_STATE_CHARS, read_state
from app.projects.memory import read_memory

BLOCK = "**Where it stands:** Login works.\n\n**Open:** Reset.\n\n**Next step:** Write the reset mail."


def start_run(client, with_project: bool = True):
    project = client.post("/api/v1/environments/1/projects", json={"name": "Site"}).json()
    folder = Path(client.app.state.workspace_dir) / project["folder"]
    registry = RunRegistry()
    run = registry.start(RunConfig(
        conversation_id=1,
        environment_id=1,
        blueprint_layers=[],
        tool_names=tools_for(has_project=with_project, has_attachments=False),
        project_id=project["id"] if with_project else None,
        project_folder=folder if with_project else None,
    ))
    return registry, run, folder


def update(registry, run, text: str):
    return asyncio.run(call_tool(registry, run, "update_current_state", {"text": text}))


def test_the_tool_runs_without_asking_and_belongs_to_project_conversations():
    tool = TOOLS_BY_NAME["update_current_state"]

    assert tool.permission == "auto" and tool is STATE_TOOLS[0]
    assert "update_current_state" in tools_for(has_project=True, has_attachments=False)
    assert "update_current_state" not in tools_for(has_project=False, has_attachments=False)


def test_the_description_says_when_to_use_it():
    description = TOOLS_BY_NAME["update_current_state"].description

    assert "end of a goal or a build" in description and "stops a long piece of work" in description
    assert str(MAX_STATE_CHARS) in description


def test_the_agent_rewrites_the_block_in_one_commit_of_only_the_memory(client):
    registry, run, folder = start_run(client)
    (folder / "draft.txt").write_text("not mine to commit\n", encoding="utf-8")

    output, error = update(registry, run, BLOCK)

    assert not error, output
    assert read_state(read_memory(folder)) == BLOCK
    log = subprocess.run(
        ["git", "log", "-1", "--name-only", "--format=%s"], cwd=folder, capture_output=True, text=True, check=True
    ).stdout
    assert log.startswith(f"{COMMIT_PREFIX}written by the agent") and ".blueprint/memory.md" in log
    assert "draft.txt" not in log


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("x" * (MAX_STATE_CHARS + 1), "at most"),
        ("   ", "empty"),
        ("**Open:** a\n## Decisions", "heading"),
    ],
    ids=["over_budget", "empty", "heading_line"],
)
def test_the_tool_refuses_an_invalid_block_and_keeps_the_old_one(client, text, message):
    registry, run, folder = start_run(client)
    assert not update(registry, run, BLOCK)[1]
    before = read_memory(folder)

    output, error = update(registry, run, text)

    assert error and message in output
    assert read_memory(folder) == before


def test_the_tool_refuses_without_a_repository(client, monkeypatch):
    registry, run, folder = start_run(client)
    monkeypatch.setattr(state_refresh, "has_repository", lambda _folder: False)

    output, error = update(registry, run, BLOCK)

    assert error and "no git repository" in output
    assert read_state(read_memory(folder)) is None


def test_the_tool_needs_a_project_folder(client):
    registry, run, _ = start_run(client, with_project=False)

    output, error = update(registry, run, BLOCK)

    assert error
