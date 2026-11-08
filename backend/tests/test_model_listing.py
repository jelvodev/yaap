"""The model lists of an environment (core/model_listing.py): per model whether it can answer, from the model choice
itself, so a model without a key says so and a model the policy forbids says which policy, with or without the
project whose policy applies as well."""

import pytest

from tests.test_chat import add_key

OPUS, SONNET = "claude-opus-5-5", "claude-sonnet-5-5"
ONLY_OPUS = {"models": [OPUS]}


def listed(client, project_id: int | None = None) -> dict[str, dict]:
    query = f"?project_id={project_id}" if project_id is not None else ""
    response = client.get(f"/api/v1/environments/1/models{query}")
    assert response.status_code == 200, response.text
    return {model["id"]: model for model in response.json()}


def make_project(client, environment_policy: dict | None, project_policy: dict | None) -> int:
    assert client.put("/api/v1/settings/anthropic_connection", json={"value": "api_key"}).status_code == 200
    if environment_policy is not None:
        saved = client.put("/api/v1/environments/1/settings/model_policy", json={"value": environment_policy})
        assert saved.status_code == 200, saved.text
    project = client.post("/api/v1/environments/1/projects", json={"name": "Site"}).json()
    if project_policy is not None:
        saved = client.put(f"/api/v1/projects/{project['id']}/settings/model_policy", json={"value": project_policy})
        assert saved.status_code == 200, saved.text
    return project["id"]


@pytest.mark.parametrize(
    ("environment_policy", "project_policy", "in_project", "model", "allowed", "refused_by"),
    [
        (None, None, False, SONNET, True, None),
        (ONLY_OPUS, None, False, OPUS, True, None),
        (ONLY_OPUS, None, False, SONNET, False, "of the environment"),
        (ONLY_OPUS, None, True, SONNET, False, "of the environment"),
        # A project's policy applies only when the list is asked for that project.
        (None, ONLY_OPUS, False, SONNET, True, None),
        (None, ONLY_OPUS, True, SONNET, False, "of the project Site"),
        (None, ONLY_OPUS, True, OPUS, True, None),
    ],
)
def test_a_model_the_policy_forbids_is_listed_as_not_allowed_with_the_policy(
    client, environment_policy: dict | None, project_policy: dict | None, in_project: bool, model: str,
    allowed: bool, refused_by: str | None,
):
    project_id = make_project(client, environment_policy, project_policy)
    add_key(client)
    found = listed(client, project_id if in_project else None)[model]
    assert (found["allowed"], found["available"]) == (allowed, allowed)
    if refused_by is None:
        assert found["unavailable_reason"] is None
    else:
        assert found["unavailable_reason"].startswith("Refused by the model policy") and refused_by in (
            found["unavailable_reason"]
        )


@pytest.mark.parametrize("in_project", [False, True])
def test_an_allowed_model_without_a_key_stays_no_key(client, in_project: bool):
    project_id = make_project(client, None, ONLY_OPUS)
    found = listed(client, project_id if in_project else None)[OPUS]
    assert found["allowed"] and not found["available"]
    assert "Add an Anthropic API key in Settings" in found["unavailable_reason"]


def test_a_project_of_another_environment_is_not_found(client):
    project = client.post("/api/v1/environments/2/projects", json={"name": "Elsewhere"}).json()
    assert client.get(f"/api/v1/environments/1/models?project_id={project['id']}").status_code == 404
