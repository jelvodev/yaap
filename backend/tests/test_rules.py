"""Remember as rule: a correction becomes a rule in a file the chosen level owns."""

import json

from app.providers.fake_provider import ScriptedResponse
from tests.test_chat import add_key, send, start_conversation
from tests.test_blueprint import commits, put


def rule(client, text: str, apply: bool = False, path: str | None = None, **level):
    return client.post("/api/v1/blueprint/rule", params=level, json={"rule": text, "path": path, "apply": apply})


def test_preview_then_apply_creates_the_level_rules_file(client, tmp_path):
    preview = rule(client, "Answer in Dutch.", environment_id=1)
    assert preview.status_code == 200
    data = preview.json()
    assert data["path"] == "standards/rules-environment.md" and not data["applied"]
    assert "+  - Answer in Dutch." in data["diff"] or "+- Answer in Dutch." in data["diff"]
    files = client.get("/api/v1/blueprint/items", params={"environment_id": 1}).json()
    assert "standards/rules-environment.md" not in [item["path"] for item in files]

    applied = rule(client, "Answer in Dutch.", apply=True, environment_id=1).json()
    assert applied["applied"]
    items = {item["path"]: item for item in client.get("/api/v1/blueprint/items", params={"environment_id": 1}).json()}
    assert "standards/rules-environment.md" in items

    # The same rule twice is refused; a second rule is added to the list.
    assert rule(client, "Answer in Dutch.", environment_id=1).status_code == 422
    second = rule(client, "Use metric units.", apply=True, environment_id=1).json()
    assert "+- Use metric units." in second["diff"] or "+  - Use metric units." in second["diff"]
    content = client.get(
        "/api/v1/blueprint/file", params={"path": "standards/rules-environment.md", "environment_id": 1}
    ).json()["content"]
    assert content.index("Answer in Dutch.") < content.index("Use metric units.")


def test_each_level_kind_has_its_own_rules_file(client):
    assert rule(client, "Be brief.").json()["path"] == "standards/rules-global.md"
    project = client.post("/api/v1/environments/1/projects", json={"name": "Shop"}).json()
    level = {"environment_id": 1, "project_id": project["id"]}
    assert rule(client, "Run the tests first.", **level).json()["path"] == "standards/rules-project.md"


def test_a_rule_goes_into_an_own_standard_or_instructions(client):
    put(client, "standards/style.md", "---\nsummary: Style\nrules:\n  - Short sentences.\n---\n\nStyle.\n", environment_id=1)
    added = rule(client, "No jargon.", apply=True, path="standards/style.md", environment_id=1).json()
    assert added["path"] == "standards/style.md"
    content = client.get("/api/v1/blueprint/file", params={"path": "standards/style.md", "environment_id": 1}).json()[
        "content"
    ]
    assert "Short sentences." in content and "No jargon." in content

    put(client, "instructions.md", "Be helpful.\n", environment_id=1)
    added = rule(client, "Ask before deleting.", apply=True, path="instructions.md", environment_id=1).json()
    assert "+- Ask before deleting." in added["diff"]

    # A standard the level only inherits is not changed: the rule goes into the level's own rules file.
    put(client, "standards/global-only.md", "---\nsummary: G\n---\n\nG.\n")
    assert rule(client, "X.", path="standards/global-only.md", environment_id=1).json()["path"] == (
        "standards/rules-environment.md"
    )


def test_rule_proposal_from_the_utility_model_and_its_fallback(client, fake):
    add_key(client)
    conversation_id = start_conversation(client)
    fake.responses.append(ScriptedResponse(text="Here is a long answer in English."))
    send(client, conversation_id, "Wat is 2+2?")
    fake.responses.append(ScriptedResponse(text="Ok."))
    send(client, conversation_id, "Antwoord altijd in het Nederlands en kort.")
    messages = client.get(f"/api/v1/conversations/{conversation_id}").json()["messages"]
    correction = messages[2]
    assert correction["role"] == "user"

    fake.responses.append(
        ScriptedResponse(text='Sure: {"rule": "Answer in Dutch and keep it short.", "level": "global", "path": null}')
    )
    proposal = client.post(f"/api/v1/conversations/{conversation_id}/messages/{correction['id']}/rule-proposal")
    assert proposal.status_code == 200
    assert proposal.json() == {
        "rule": "Answer in Dutch and keep it short.",
        "level": "global",
        "path": None,
        "from_model": True,
    }
    prompt = json.dumps([part for part in fake.requests[-1].messages], default=str)
    assert "Antwoord altijd in het Nederlands" in prompt and "long answer in English" in prompt

    # No usable answer: the user's own text, at the environment level for a conversation without a project.
    fake.responses.append(ScriptedResponse(text="I cannot help with that."))
    fallback = client.post(
        f"/api/v1/conversations/{conversation_id}/messages/{correction['id']}/rule-proposal"
    ).json()
    assert fallback == {
        "rule": "Antwoord altijd in het Nederlands en kort.",
        "level": "environment",
        "path": None,
        "from_model": False,
    }

    # Only the user's own messages.
    reply = messages[1]
    refused = client.post(f"/api/v1/conversations/{conversation_id}/messages/{reply['id']}/rule-proposal")
    assert refused.status_code == 422
