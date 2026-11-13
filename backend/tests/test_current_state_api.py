"""GET and PUT /projects/{id}/current-state, and the block in the first turn of a project conversation."""

import subprocess
from pathlib import Path

import pytest

from app.projects import state_refresh
from app.projects.current_state import COMMIT_PREFIX, MAX_STATE_CHARS
from app.projects.memory import TEMPLATE, add_entry
from app.providers.fake_provider import ScriptedResponse
from tests.test_chat import add_key

BLOCK = "**Where it stands:** Login works.\n\n**Open:** Reset.\n\n**Next step:** Write the reset mail."


def make_project(client) -> tuple[dict, str, Path]:
    project = client.post("/api/v1/environments/1/projects", json={"name": "Site"}).json()
    return project, f"/api/v1/projects/{project['id']}/current-state", Path(client.app.state.workspace_dir) / project["folder"]


def test_a_new_project_has_an_empty_block_that_can_be_written(client):
    _, url, _ = make_project(client)

    body = client.get(url).json()

    assert body == {"text": "", "updated_at": None, "max_chars": MAX_STATE_CHARS, "has_repository": True}


def test_saving_the_block_commits_it_and_returns_it_with_its_time(client):
    _, url, folder = make_project(client)

    saved = client.put(url, json={"text": f"\n{BLOCK}\n"})

    assert saved.status_code == 200
    assert saved.json()["text"] == BLOCK and saved.json()["updated_at"] is not None
    assert client.get(url).json()["text"] == BLOCK
    subject = subprocess.run(
        ["git", "log", "-1", "--format=%s"], cwd=folder, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert subject == f"{COMMIT_PREFIX}edited by you"


def test_saving_keeps_the_entries_of_the_memory(client):
    project, url, _ = make_project(client)
    memory = f"/api/v1/projects/{project['id']}/memory"
    client.put(memory, json={"content": add_entry(TEMPLATE, "Decisions", "Use SQLite.")})

    assert client.put(url, json={"text": BLOCK}).status_code == 200

    content = client.get(memory).json()["content"]
    assert "- Use SQLite." in content and content.index("## Current state") < content.index("## Decisions")
    entries = client.get(f"{memory}/entries").json()["entries"]
    assert [item["text"] for item in entries] == ["Use SQLite."]


@pytest.mark.parametrize(
    "text",
    ["", "   ", "x" * (MAX_STATE_CHARS + 1), "**Open:** a\n## Decisions\nb"],
    ids=["empty", "blank", "over_budget", "heading_line"],
)
def test_an_invalid_block_is_refused_with_422(client, text):
    _, url, _ = make_project(client)

    response = client.put(url, json={"text": text})

    assert response.status_code == 422
    assert client.get(url).json()["text"] == ""


def test_a_project_without_a_repository_refuses_with_409(client, monkeypatch):
    _, url, _ = make_project(client)
    monkeypatch.setattr(state_refresh, "has_repository", lambda _folder: False)

    response = client.put(url, json={"text": BLOCK})

    assert response.status_code == 409


def test_an_unknown_project_is_not_found(client):
    assert client.get("/api/v1/projects/999/current-state").status_code == 404
    assert client.put("/api/v1/projects/999/current-state", json={"text": BLOCK}).status_code == 404


def test_the_block_leads_the_system_prompt_of_the_first_turn_and_is_not_an_entry(client, fake):
    add_key(client)
    project, url, _ = make_project(client)
    client.put(f"/api/v1/projects/{project['id']}/memory", json={"content": add_entry(TEMPLATE, "Decisions", "Use SQLite.")})
    client.put(url, json={"text": BLOCK})
    conversation_id = client.post(
        "/api/v1/environments/1/conversations",
        json={"model": "claude-opus-5-5", "effort": "medium", "project_id": project["id"]},
    ).json()["id"]
    fake.responses.append(ScriptedResponse(text="Hello."))

    client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Where were we?"})

    system = "\n\n".join(part.text for part in fake.requests[0].system)
    assert "### Where this project stands" in system and "Write the reset mail." in system
    assert system.index("### Where this project stands") < system.index("- Use SQLite.")
    assert system.count("Write the reset mail.") == 1
