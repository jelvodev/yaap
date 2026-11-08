from app.agent.tools import tools_for
from app.providers.fake_provider import ScriptedResponse


def add_template(client, name: str, environment_id: int = 1):
    return client.post(f"/api/v1/environments/{environment_id}/agents/from-template", json={"template": name})


def test_agent_templates_become_blueprint_items(client):
    templates = {template["name"] for template in client.get("/api/v1/agent-templates").json()}
    assert templates == {"developer", "reviewer", "architect"}

    created = add_template(client, "reviewer")
    assert created.status_code == 201
    reviewer = created.json()
    assert reviewer["preset"] == "Deep"
    assert "write_file" not in reviewer["tools"] and "git_diff" in reviewer["tools"]
    assert reviewer["source_label"] == "Private"
    assert add_template(client, "reviewer").status_code == 409

    # The agent is a normal Blueprint item of the environment, and only of that environment.
    items = client.get("/api/v1/blueprint/items", params={"environment_id": 1}).json()
    assert any(item["path"] == "agents/reviewer.md" for item in items)
    assert client.get("/api/v1/environments/2/agents").json() == []


def test_unknown_tools_in_a_definition_are_reported(client):
    content = "---\ndescription: Test\ntools: [read_file, teleport]\n---\n\nBe brief.\n"
    client.put(
        "/api/v1/blueprint/file",
        params={"path": "agents/tester.md", "environment_id": 1},
        json={"content": content},
    )
    agent = next(agent for agent in client.get("/api/v1/environments/1/agents").json() if agent["name"] == "tester")
    assert agent["tools"] == ["read_file"]
    assert agent["warnings"] == ["Unknown tools are ignored: teleport."]


def test_a_conversation_works_as_its_agent(client, fake):
    client.put("/api/v1/settings/anthropic_connection", json={"value": "claude_code"})
    add_template(client, "reviewer")
    project = client.post("/api/v1/environments/1/projects", json={"name": "Site"}).json()
    unknown = client.post(
        "/api/v1/environments/1/conversations",
        json={"model": "claude-opus-5-5", "effort": "high", "project_id": project["id"], "agent": "ghost"},
    )
    assert unknown.status_code == 422
    conversation = client.post(
        "/api/v1/environments/1/conversations",
        json={"model": "claude-opus-5-5", "effort": "high", "project_id": project["id"], "agent": "reviewer"},
    ).json()
    assert conversation["agent"] == "reviewer"
    fake.responses.append(ScriptedResponse(text="No findings."))

    client.post(f"/api/v1/conversations/{conversation['id']}/messages", json={"text": "Review the changes"})

    system = "\n\n".join(part.text for part in fake.requests[0].system)
    assert "# Your role: reviewer" in system
    assert system.index("# Your role: reviewer") < system.index("# Project")
    runs = client.get("/api/v1/environments/1/runs").json()
    assert len(runs) == 1
    run = runs[0]
    assert run["agent"] == "reviewer" and run["status"] == "complete" and run["project_name"] == "Site"
    assert run["finished_at"] is not None

    cleared = client.patch(f"/api/v1/conversations/{conversation['id']}", json={"agent": None}).json()
    assert cleared["agent"] is None
    renamed = client.patch(f"/api/v1/conversations/{conversation['id']}", json={"title": "Review"}).json()
    assert renamed["title"] == "Review" and renamed["agent"] is None


def test_agent_tools_narrow_what_the_run_offers():
    everything = tools_for(has_project=True, has_attachments=False)
    narrowed = tools_for(has_project=True, has_attachments=False, allowed={"read_file", "git_diff", "read_attachment"})
    assert "write_file" in everything
    # An agent never gets a tool the conversation does not offer (no attachments here).
    assert narrowed == {"read_file", "git_diff"}
