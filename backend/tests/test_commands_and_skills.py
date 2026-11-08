from app.providers.fake_provider import ScriptedResponse
from tests.conftest import VALID_KEY, parse_events


def put_item(client, path: str, content: str, environment_id: int = 1) -> None:
    response = client.put(
        "/api/v1/blueprint/file", params={"path": path, "environment_id": environment_id}, json={"content": content}
    )
    assert response.status_code == 200, response.text


def start(client, **extra) -> int:
    client.put("/api/v1/environments/1/credentials/anthropic_api_key", json={"value": VALID_KEY})
    body = {"model": "claude-opus-5-5", "effort": "medium", **extra}
    response = client.post("/api/v1/environments/1/conversations", json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def send(client, conversation_id: int, text: str) -> list:
    return parse_events(client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": text}).text)


def last_user_text(fake) -> str:
    # Without the per-message context block (date and time), which every request carries.
    return "\n".join(
        block.get("text", "")
        for block in fake.requests[-1].messages[-1].content
        if not block.get("text", "").startswith("[Context for this message]")
    )


def test_a_command_is_filled_in_for_the_model_and_kept_as_typed(client, fake):
    put_item(client, "commands/review.md", "---\ndescription: Review a path\n---\n\nReview $ARGUMENTS for bugs.\n")
    conversation_id = start(client)
    fake.responses.append(ScriptedResponse(text="Looks fine."))

    events = send(client, conversation_id, "/review src/api")

    stored = events[0][1]["content"]
    assert [block["type"] for block in stored] == ["text", "command"]
    assert stored[0]["text"] == "/review src/api"
    assert last_user_text(fake) == "Review src/api for bugs."


def test_unknown_commands_stay_text(client, fake):
    conversation_id = start(client)
    fake.responses.append(ScriptedResponse(text="?"))
    send(client, conversation_id, "/nothing here")
    assert last_user_text(fake) == "/nothing here"


def test_template_without_placeholder_gets_the_arguments_appended(client, fake):
    put_item(client, "commands/plan.md", "Make a plan.\n")
    conversation_id = start(client)
    fake.responses.append(ScriptedResponse(text="Plan."))
    send(client, conversation_id, "/plan the login page")
    assert last_user_text(fake) == "Make a plan.\n\nthe login page"


def test_pinned_skills_reach_the_model_once_and_unpinning_is_noted(client, fake):
    put_item(client, "skills/tdd/SKILL.md", "---\ndescription: Test first\n---\n\nWrite the failing test first.\n")
    conversation_id = start(client, pinned_skills=["tdd"])
    assert client.get(f"/api/v1/conversations/{conversation_id}").json()["pinned_skills"] == ["tdd"]
    fake.responses.extend([ScriptedResponse(text="One."), ScriptedResponse(text="Two."), ScriptedResponse(text="Three.")])

    send(client, conversation_id, "Add a parser")
    first = last_user_text(fake)
    assert '<skill name="tdd">\nWrite the failing test first.\n</skill>' in first

    send(client, conversation_id, "And a printer")
    # The skill text is in the history once, not again with every message.
    assert '<skill name="tdd">' not in last_user_text(fake)
    assert sum('<skill name="tdd">' in block.get("text", "") for message in fake.requests[-1].messages for block in message.content) == 1

    unpinned = client.put(f"/api/v1/conversations/{conversation_id}/pinned-skills", json={"skills": []})
    assert unpinned.json()["pinned_skills"] == []
    send(client, conversation_id, "Done")
    assert "[The skill tdd no longer applies from here on.]" in last_user_text(fake)


def test_unknown_skills_cannot_be_pinned(client):
    conversation_id = start(client)
    response = client.put(f"/api/v1/conversations/{conversation_id}/pinned-skills", json={"skills": ["ghost"]})
    assert response.status_code == 422


def test_an_agent_brings_its_skills_and_limits_commands(client, fake):
    put_item(client, "skills/style/SKILL.md", "Short sentences.\n")
    put_item(client, "commands/review.md", "Review $ARGUMENTS.\n")
    put_item(client, "commands/deploy.md", "Deploy $ARGUMENTS.\n")
    put_item(client, "agents/writer.md", "---\ndescription: Writes\nskills: [style]\ncommands: [review]\n---\n\nWrite well.\n")
    conversation_id = start(client, agent="writer")
    fake.responses.extend([ScriptedResponse(text="a"), ScriptedResponse(text="b")])

    send(client, conversation_id, "/deploy now")
    system = "\n\n".join(part.text for part in fake.requests[-1].system)
    assert '<skill name="style">\nShort sentences.\n</skill>' in system
    assert last_user_text(fake) == "/deploy now"
    send(client, conversation_id, "/review docs")
    assert last_user_text(fake) == "Review docs."


def test_default_skills_follow_the_nearest_level_and_skip_missing_ones(client):
    put_item(client, "skills/caveman/SKILL.md", "---\ndescription: Terse answers\n---\n\nShort.\n")
    put_item(client, "skills/house/SKILL.md", "House style.\n")
    assert client.get("/api/v1/environments/1/default-skills").json() == {"skills": [], "missing": []}

    assert client.put("/api/v1/settings/default_skills", json={"value": ["caveman", "gone"]}).status_code == 200
    assert client.get("/api/v1/environments/1/default-skills").json() == {"skills": ["caveman"], "missing": ["gone"]}

    project = client.post("/api/v1/environments/1/projects", json={"name": "Styled"}).json()
    client.put(f"/api/v1/projects/{project['id']}/settings/default_skills", json={"value": ["house"]})
    scoped = client.get("/api/v1/environments/1/default-skills", params={"project_id": project["id"]}).json()
    assert scoped["skills"] == ["house"]
    assert client.get("/api/v1/environments/2/default-skills", params={"project_id": project["id"]}).status_code == 404
    assert client.put("/api/v1/settings/default_skills", json={"value": "caveman"}).status_code == 422
