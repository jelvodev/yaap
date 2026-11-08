"""Blueprint work: /check against the project's standards, and new projects from a template."""

import subprocess
from pathlib import Path

import httpx

from app.github import client as github_client
from app.providers.fake_provider import ScriptedResponse
from tests.test_commands_and_skills import last_user_text, put_item, send, start
from tests.test_github import TOKEN, FakeGitHub, store_token

STANDARD = "---\nsummary: {summary}\n---\n{body}\n"


def add_standards(client):
    put_item(client, "standards/naming.md", STANDARD.format(summary="Clear names", body="Use full words in names."))
    put_item(client, "standards/tests.md", STANDARD.format(summary="Tests", body="Every module has tests."))


def project_conversation(client) -> int:
    project = client.post("/api/v1/environments/1/projects", json={"name": "Site"}).json()
    return start(client, project_id=project["id"])


def test_check_lists_the_standards_and_runs_read_only(client, fake, tmp_path):
    add_standards(client)
    conversation_id = project_conversation(client)
    fake.responses.extend(
        [ScriptedResponse(text="Checked.", tool_calls=(("write_file", {"path": "x.txt", "content": "x"}),))]
    )
    events = send(client, conversation_id, "/check")
    assert not [event for event, _ in events if event == "error"], events
    text = last_user_text(fake)
    assert "Standards to check:\n1. naming" in text and "2. tests" in text
    assert "**Verdict:** follows | deviates | not applicable" in text
    # The run is read only: the write was refused, and the conversation's own access is unchanged.
    names = [event for event, _ in events]
    finished = [data for event, data in events if event.startswith("tool") and "Refused" in str(data)]
    assert finished and "read only" in str(finished[0]), names
    assert not list(Path(tmp_path).rglob("x.txt"))
    assert client.get(f"/api/v1/conversations/{conversation_id}").json()["access"] == "default"


def test_check_one_standard_or_refuse_an_unknown_one(client, fake):
    add_standards(client)
    conversation_id = project_conversation(client)
    fake.responses.append(ScriptedResponse(text="Checked."))
    send(client, conversation_id, "/check tests")
    text = last_user_text(fake)
    assert "1. tests" in text and "naming" not in text.split("Report in exactly")[0]
    refused = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "/check nothing"})
    assert refused.status_code == 409 and "no standard named nothing" in refused.json()["detail"]


def test_check_needs_a_project(client):
    add_standards(client)
    conversation_id = start(client)
    refused = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "/check"})
    assert refused.status_code == 409 and "project conversation" in refused.json()["detail"]


def test_a_new_project_from_a_template_gets_its_repository_clone_and_blueprint(client, monkeypatch, tmp_path):
    fake = FakeGitHub()
    monkeypatch.setattr(github_client, "DEFAULT_TRANSPORT", httpx.MockTransport(fake.handle))
    store_token(client)
    cloned: list[str] = []

    def clone(url: str, target: Path, token: str | None) -> None:
        cloned.append(url)
        (target / ".blueprint" / "standards").mkdir(parents=True)
        (target / ".blueprint" / "standards" / "shop.md").write_text("---\nsummary: Shop rules\n---\nKeep prices exact.\n")
        subprocess.run(["git", "init", "-q", str(target)], check=True)

    monkeypatch.setattr("app.projects.service.clone_repository", clone)
    created = client.post(
        "/api/v1/environments/1/projects", json={"name": "Shop", "template": "me/starter", "repository_name": "shop"}
    )
    assert created.status_code == 201, created.text
    assert ("POST", "/repos/me/starter/generate", {"name": "shop", "private": True, "description": ""}) in fake.requests
    assert cloned == ["https://github.com/me/shop.git"]
    items = client.get("/api/v1/blueprint/items", params={"environment_id": 1, "project_id": created.json()["id"]}).json()
    assert "shop" in {item["name"] for item in items}
    assert client.post("/api/v1/environments/1/projects", json={"name": "X", "template": "not a repo"}).status_code == 422
    assert TOKEN  # The token came from the vault, never from the request.
