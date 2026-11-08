def test_default_environments_are_seeded(client):
    environments = client.get("/api/v1/environments").json()
    assert [environment["name"] for environment in environments] == ["Private", "Work"]


def test_create_and_rename_environment(client):
    created = client.post("/api/v1/environments", json={"name": "Side project", "colour": "#10b981"})
    assert created.status_code == 201
    assert created.json()["colour"] == "#10B981"

    renamed = client.patch(f"/api/v1/environments/{created.json()['id']}", json={"name": "Hobby"})
    assert renamed.json()["name"] == "Hobby"


def test_environment_names_are_unique_case_insensitive(client):
    response = client.post("/api/v1/environments", json={"name": "work", "colour": "#000000"})
    assert response.status_code == 409


def test_invalid_colour_is_rejected(client):
    response = client.post("/api/v1/environments", json={"name": "Other", "colour": "orange"})
    assert response.status_code == 422
