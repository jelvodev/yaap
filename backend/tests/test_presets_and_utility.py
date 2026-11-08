from sqlalchemy import select

from app.providers.fake_provider import ScriptedResponse
from app.storage.models import UsageRecord
from tests.conftest import VALID_KEY, parse_events


def presets(client, environment_id: int = 1) -> list[dict]:
    return client.get(f"/api/v1/environments/{environment_id}/presets").json()


def test_environments_start_with_four_presets(client):
    names = [(preset["name"], preset["model"], preset["effort"], preset["is_default"]) for preset in presets(client)]
    assert names == [
        ("Fast", "claude-sonnet-5-5", "low", False),
        ("Balanced", "claude-opus-5-5", "medium", True),
        ("Deep", "claude-opus-5-5", "high", False),
        ("Cheap", "claude-haiku-5-5", "low", False),
    ]
    created = client.post("/api/v1/environments", json={"name": "Lab", "colour": "#22AA88"}).json()
    assert len(presets(client, created["id"])) == 4


def test_presets_can_be_added_changed_and_removed(client):
    added = client.post(
        "/api/v1/environments/1/presets", json={"name": " Review ", "model": "claude-opus-5-5", "effort": "xhigh"}
    )
    assert added.status_code == 201 and added.json()["name"] == "Review"
    duplicate = client.post("/api/v1/environments/1/presets", json={"name": "review", "model": "claude-opus-5-5", "effort": "low"})
    assert duplicate.status_code == 422
    bad_effort = client.patch(f"/api/v1/presets/{added.json()['id']}", json={"effort": "extreme"})
    assert bad_effort.status_code == 422

    made_default = client.patch(f"/api/v1/presets/{added.json()['id']}", json={"is_default": True}).json()
    assert made_default["is_default"] is True
    assert [preset["name"] for preset in presets(client) if preset["is_default"]] == ["Review"]

    # Removing the default hands it to the first preset.
    assert client.delete(f"/api/v1/presets/{added.json()['id']}").status_code == 204
    assert [preset["name"] for preset in presets(client) if preset["is_default"]] == ["Fast"]


def test_the_last_preset_stays(client):
    for preset in presets(client, 2)[:-1]:
        assert client.delete(f"/api/v1/presets/{preset['id']}").status_code == 204
    last = presets(client, 2)[0]
    assert client.delete(f"/api/v1/presets/{last['id']}").status_code == 409


def test_first_reply_gets_a_title_from_the_utility_model(client, fake):
    client.app.state.utility_titles = True
    client.put("/api/v1/environments/1/credentials/anthropic_api_key", json={"value": VALID_KEY})
    conversation_id = client.post(
        "/api/v1/environments/1/conversations", json={"model": "claude-opus-5-5", "effort": "medium"}
    ).json()["id"]
    fake.responses.extend([ScriptedResponse(text="Gebruik een venv."), ScriptedResponse(text='"Python omgeving opzetten."')])

    events = parse_events(
        client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "hoe zet ik python op"}).text
    )

    assert [name for name, _ in events][-2:] == ["done", "title"]
    assert events[-1][1]["conversation"]["title"] == "Python omgeving opzetten"
    utility_request = fake.requests[1]
    assert utility_request.model == "claude-haiku-5-5" and utility_request.effort == "low"
    with client.app.state.session_factory() as session:
        purposes = [record.purpose for record in session.scalars(select(UsageRecord).order_by(UsageRecord.id))]
    assert purposes == ["main", "utility"]
    events = client.get(f"/api/v1/conversations/{conversation_id}").json()["utility_events"]
    assert [(event["task"], event["summary"]) for event in events] == [("title", "Python omgeving opzetten")]

    # Later replies keep the title.
    fake.responses.append(ScriptedResponse(text="Graag gedaan."))
    later = parse_events(client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "dank"}).text)
    assert later[-1][0] == "done"


def test_commit_message_is_proposed_from_the_changes(client, fake):
    client.put("/api/v1/environments/1/credentials/anthropic_api_key", json={"value": VALID_KEY})
    project = client.post("/api/v1/environments/1/projects", json={"name": "Site"}).json()
    repository = f"/api/v1/environments/1/git/repositories/project-{project['id']}"
    assert client.post(f"{repository}/commit-message").status_code == 409

    from pathlib import Path

    folder = Path(client.app.state.workspace_dir) / project["folder"]
    (folder / "index.html").write_text("<h1>Hi</h1>\n", encoding="utf-8")
    fake.responses.append(ScriptedResponse(text="Add the home page"))

    proposed = client.post(f"{repository}/commit-message")

    assert proposed.status_code == 200
    assert proposed.json()["message"] == "Add the home page"
    assert "index.html" in fake.requests[0].messages[0].content[0]["text"]


def test_interface_preferences_are_global_and_checked(client):
    setting = next(item for item in client.get("/api/v1/settings").json() if item["key"] == "interface")
    assert setting["value"] == {"theme": "dark", "utility_messages": False, "verbose_tools": False}
    saved = client.put("/api/v1/settings/interface", json={"value": {"theme": "light"}})
    assert saved.status_code == 200 and saved.json()["value"]["theme"] == "light"
    assert saved.json()["value"]["verbose_tools"] is False
    assert client.put("/api/v1/settings/interface", json={"value": {"theme": "pink"}}).status_code == 422
    assert client.put("/api/v1/settings/interface", json={"value": {"other": 1}}).status_code == 422
    assert client.put("/api/v1/environments/1/settings/interface", json={"value": {"theme": "dark"}}).status_code in (404, 422)


def test_presets_hold_models_by_role(client):
    balanced = next(preset for preset in presets(client) if preset["name"] == "Balanced")
    assert (balanced["worker_model"], balanced["worker_effort"]) == ("claude-sonnet-5-5", "low")
    assert balanced["utility_model"] is None

    changed = client.patch(f"/api/v1/presets/{balanced['id']}", json={"utility_model": "claude-sonnet-5-5"}).json()
    assert (changed["utility_model"], changed["utility_effort"]) == ("claude-sonnet-5-5", "high")  # its default
    assert changed["worker_model"] == "claude-sonnet-5-5"  # untouched
    assert client.patch(f"/api/v1/presets/{balanced['id']}", json={"worker_effort": "extreme"}).status_code == 422
    cleared = client.patch(f"/api/v1/presets/{balanced['id']}", json={"worker_model": None}).json()
    assert cleared["worker_model"] is None and cleared["worker_effort"] is None
    added = client.post(
        "/api/v1/environments/1/presets",
        json={"name": "Lean", "model": "claude-sonnet-5-5", "effort": "low", "worker_model": "claude-haiku-5-5"},
    ).json()
    assert (added["worker_model"], added["worker_effort"]) == ("claude-haiku-5-5", "medium")


def test_the_conversation_preset_chooses_the_utility_model(client, fake):
    client.app.state.utility_titles = True
    client.put("/api/v1/environments/1/credentials/anthropic_api_key", json={"value": VALID_KEY})
    fast = next(preset for preset in presets(client) if preset["name"] == "Fast")
    client.patch(f"/api/v1/presets/{fast['id']}", json={"utility_model": "claude-sonnet-5-5", "utility_effort": "low"})
    conversation_id = client.post(
        "/api/v1/environments/1/conversations",
        json={"model": "claude-sonnet-5-5", "effort": "low", "preset": "Fast"},
    ).json()["id"]
    fake.responses.extend([ScriptedResponse(text="Hoi."), ScriptedResponse(text="Groet")])
    client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "hallo"})
    assert (fake.requests[1].model, fake.requests[1].effort) == ("claude-sonnet-5-5", "low")
