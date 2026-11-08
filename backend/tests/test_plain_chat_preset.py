"""A default preset for plain chats: the environment setting plain_chat_preset chooses the preset a
conversation outside a project starts with, apart from the project's preset and the default preset."""

from app.core.api_conversations import Scope, new_conversation
from app.presets.service import starting_preset
from app.storage.models import Environment, Project

SETTING = "/api/v1/environments/1/settings/plain_chat_preset"


def flagged(client) -> list[str]:
    return [preset["name"] for preset in client.get("/api/v1/environments/1/presets").json() if preset["plain_chats"]]


def stored(client) -> dict:
    setting = next(item for item in client.get("/api/v1/environments/1/settings").json() if item["key"] == "plain_chat_preset")
    return {"value": setting["value"], "source": setting["source"]}


def started(client, project_id: int | None) -> str | None:
    with client.app.state.session_factory() as session:
        project = session.get(Project, project_id) if project_id is not None else None
        preset = starting_preset(session, 1, project)
        return preset.name if preset is not None else None


def test_unset_falls_back_to_the_default_preset(client):
    assert flagged(client) == []
    assert started(client, None) == "Balanced"


def test_the_setting_chooses_the_preset_of_a_plain_chat_but_not_of_a_project_conversation(client):
    assert client.put(SETTING, json={"value": "Cheap"}).status_code == 200
    assert flagged(client) == ["Cheap"]
    assert started(client, None) == "Cheap"

    project = client.post("/api/v1/environments/1/projects", json={"name": "Shop"}).json()
    assert started(client, project["id"]) == "Balanced"
    client.patch(f"/api/v1/projects/{project['id']}", json={"default_preset": "Deep"})
    assert started(client, project["id"]) == "Deep"

    # Conversations that API tokens start follow the same choice.
    with client.app.state.session_factory() as session:
        environment = session.get_one(Environment, 1)
        plain = new_conversation(session, client.app.state.providers, Scope(environment, None), "Plain")
        in_project = new_conversation(
            session, client.app.state.providers, Scope(environment, session.get_one(Project, project["id"])), "Work"
        )
        assert (plain.preset, plain.model) == ("Cheap", "claude-haiku-5-5")
        assert in_project.preset == "Deep"
        session.rollback()


def test_a_renamed_preset_stays_chosen_and_a_removed_one_falls_back(client):
    presets = {preset["name"]: preset for preset in client.get("/api/v1/environments/1/presets").json()}
    client.put(SETTING, json={"value": "Fast"})
    renamed = client.patch(f"/api/v1/presets/{presets['Fast']['id']}", json={"name": "Quick"}).json()
    assert renamed["plain_chats"] is True
    assert stored(client) == {"value": "Quick", "source": "environment"}

    assert client.delete(f"/api/v1/presets/{presets['Fast']['id']}").status_code == 204
    assert started(client, None) == "Balanced"
    assert stored(client) == {"value": "", "source": "default"}


def test_a_name_without_a_preset_falls_back_and_the_setting_is_per_environment(client):
    client.put(SETTING, json={"value": "Nobody"})
    assert started(client, None) == "Balanced"
    assert flagged(client) == []
    assert client.put(SETTING, json={"value": "x" * 41}).status_code == 422
    assert client.put("/api/v1/settings/plain_chat_preset", json={"value": "Cheap"}).status_code == 422
