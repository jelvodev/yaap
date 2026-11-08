from pathlib import Path


def create_project(client, name: str, environment_id: int = 1):
    return client.post(f"/api/v1/environments/{environment_id}/projects", json={"name": name})


def test_environments_get_a_folder_name(client):
    created = client.post("/api/v1/environments", json={"name": "Side Projects!", "colour": "#10B981"})
    assert created.status_code == 201
    project = create_project(client, "Demo", created.json()["id"]).json()
    assert project["folder"] == "side-projects/demo"


def test_new_project_gets_an_empty_git_folder(client, tmp_path):
    response = create_project(client, "My Website")
    assert response.status_code == 201
    project = response.json()
    assert project["folder"] == "private/my-website"
    folder = Path(tmp_path) / "workspace" / "private" / "my-website"
    assert (folder / ".git").is_dir()


def test_same_name_gets_a_separate_folder(client):
    first = create_project(client, "Tools").json()
    second = create_project(client, "Tools").json()
    assert first["folder"] == "private/tools"
    assert second["folder"] == "private/tools-2"


def test_projects_are_listed_per_environment(client):
    create_project(client, "Private project", 1)
    create_project(client, "Work project", 2)
    private = client.get("/api/v1/environments/1/projects").json()
    work = client.get("/api/v1/environments/2/projects").json()
    assert [project["name"] for project in private] == ["Private project"]
    assert [project["name"] for project in work] == ["Work project"]


def test_rename_keeps_the_folder(client):
    project = create_project(client, "Old name").json()
    renamed = client.patch(f"/api/v1/projects/{project['id']}", json={"name": "New name"}).json()
    assert renamed["name"] == "New name"
    assert renamed["folder"] == project["folder"]


def test_conversation_in_a_project(client):
    project = create_project(client, "Chat project").json()
    created = client.post(
        "/api/v1/environments/1/conversations",
        json={"model": "claude-opus-5-5", "effort": "medium", "project_id": project["id"]},
    )
    assert created.status_code == 201
    assert created.json()["project_id"] == project["id"]
    listed = client.get("/api/v1/environments/1/conversations").json()
    assert listed[0]["project_id"] == project["id"]


def test_conversation_cannot_use_a_project_of_another_environment(client):
    work_project = create_project(client, "Work only", 2).json()
    response = client.post(
        "/api/v1/environments/1/conversations",
        json={"model": "claude-opus-5-5", "effort": "medium", "project_id": work_project["id"]},
    )
    assert response.status_code == 422


def test_project_with_conversations_is_not_removed(client, tmp_path):
    project = create_project(client, "Keep me").json()
    client.post(
        "/api/v1/environments/1/conversations",
        json={"model": "claude-opus-5-5", "effort": "medium", "project_id": project["id"]},
    )
    refused = client.delete(f"/api/v1/projects/{project['id']}")
    assert refused.status_code == 409

    empty = create_project(client, "Remove me").json()
    assert client.delete(f"/api/v1/projects/{empty['id']}").status_code == 204
    # The folder stays on disk.
    assert (Path(tmp_path) / "workspace" / empty["folder"]).is_dir()


def test_a_project_moves_to_another_environment_with_its_conversations(client, fake, tmp_path):
    from sqlalchemy import select

    from app.providers.fake_provider import ScriptedResponse
    from app.storage.models import Message
    from tests.test_chat import add_key, send

    add_key(client)
    client.post("/api/v1/environments/1/presets", json={"name": "Private only", "model": "claude-opus-5-5", "effort": "low"})
    project = create_project(client, "Traveller").json()
    client.patch(f"/api/v1/projects/{project['id']}", json={"default_preset": "Private only"})
    client.put(f"/api/v1/projects/{project['id']}/settings/command_allowlist", json={"value": ["pytest"]})
    (Path(tmp_path) / "workspace" / project["folder"] / "notes.md").write_text("keep me\n")
    conversation = client.post(
        "/api/v1/environments/1/conversations",
        json={"model": "claude-opus-5-5", "effort": "medium", "project_id": project["id"]},
    ).json()["id"]
    fake.responses.append(ScriptedResponse(text="Hi"))
    send(client, conversation, "Hello")
    with client.app.state.session_factory() as session:
        reply = session.scalar(select(Message).where(Message.conversation_id == conversation, Message.role == "assistant"))
        reply.provider_raw = [{"type": "claude_code_session", "session_id": "old"}, {"type": "text", "text": "Hi"}]
        session.commit()

    assert client.post(f"/api/v1/projects/{project['id']}/move", json={"environment_id": 1}).status_code == 409
    assert client.post(f"/api/v1/projects/{project['id']}/move", json={"environment_id": 99}).status_code == 404
    moved = client.post(f"/api/v1/projects/{project['id']}/move", json={"environment_id": 2})
    assert moved.status_code == 200, moved.text
    body = moved.json()
    assert body["project"]["folder"] == "work/traveller" and body["project"]["environment_id"] == 2
    assert body["cleared"] == ["preset Private only"] and body["project"]["default_preset"] is None

    assert (Path(tmp_path) / "workspace" / "work" / "traveller" / "notes.md").read_text() == "keep me\n"
    assert not (Path(tmp_path) / "workspace" / "private" / "traveller").exists()
    assert [item["id"] for item in client.get("/api/v1/environments/2/conversations").json()] == [conversation]
    assert client.get("/api/v1/environments/1/conversations").json() == []
    settings = client.get(f"/api/v1/projects/{project['id']}/settings").json()
    setting = next(item for item in settings if item["key"] == "command_allowlist")
    assert setting["value"] == ["pytest"] and setting["source"] == "project"
    with client.app.state.session_factory() as session:
        reply = session.scalar(select(Message).where(Message.conversation_id == conversation, Message.role == "assistant"))
        assert reply.provider_raw == [{"type": "text", "text": "Hi"}]
    # Usage stays with the environment whose connection paid for it.
    assert client.get("/api/v1/environments/1/usage/report").json()["totals"]["calls"] >= 1
