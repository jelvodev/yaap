import io
import zipfile

from app.providers.fake_provider import ScriptedResponse
from tests.conftest import VALID_KEY

STANDARD = "# API design\n\nUse plural nouns for collections.\nReturn problem details on errors.\n"


def zip_of(files: dict[str, str]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return output.getvalue()


def analyze(client, name: str, data: bytes, **params):
    return client.post(
        "/api/v1/blueprint/import/analyze",
        params={"environment_id": 1, **params},
        files={"file": (name, data)},
    )


def test_documents_get_proposals_from_the_utility_model(client, fake):
    client.put("/api/v1/environments/1/credentials/anthropic_api_key", json={"value": VALID_KEY})
    fake.responses.append(
        ScriptedResponse(
            text='{"kind": "standard", "name": "API Design", "summary": "How YAAP APIs look.", '
            '"rules": ["Use plural nouns for collections.", "Return problem details on errors."]}'
        )
    )

    response = analyze(client, "api.md", STANDARD.encode(), utility_environment_id=1)

    assert response.status_code == 200, response.text
    [proposal] = response.json()
    assert proposal["kind"] == "standard" and proposal["name"] == "api-design"
    assert proposal["path"] == "standards/api-design.md" and proposal["exists"] is False
    assert proposal["rules"] == ["Use plural nouns for collections.", "Return problem details on errors."]
    assert proposal["proposed_by_model"] is True
    assert fake.requests[0].model == "claude-haiku-5-5"


def test_a_zip_is_read_and_existing_front_matter_is_kept(client):
    data = zip_of(
        {
            "library/skills/code-review/SKILL.md": "---\ndescription: Review code\n---\n\nRead the diff first.\n",
            "library/agents/planner.md": "---\ndescription: Plans work\npreset: Deep\n---\n\nPlan first.\n",
            "library/notes.txt": "not markdown",
            "library/empty.md": "---\ntitle: x\n---\n",
        }
    )
    proposals = {item["path"]: item for item in analyze(client, "library.zip", data).json()}

    assert set(proposals) == {"skills/code-review/SKILL.md", "agents/planner.md"}
    assert proposals["agents/planner.md"]["extra"] == {"preset": "Deep"}
    assert proposals["skills/code-review/SKILL.md"]["summary"] == "Review code"


def test_reviewed_items_are_written_as_blueprint_files(client):
    items = [
        {"kind": "standard", "name": "api-design", "summary": "How APIs look.", "rules": ["Plural nouns."], "body": STANDARD},
        {"kind": "agent", "name": "planner", "summary": "Plans work", "body": "Plan first.", "extra": {"preset": "Deep"}},
    ]
    applied = client.post("/api/v1/blueprint/import/apply", params={"environment_id": 1}, json={"items": items})
    assert applied.status_code == 200, applied.text
    assert applied.json()["written"] == ["standards/api-design.md", "agents/planner.md"]

    standard = client.get("/api/v1/blueprint/file", params={"environment_id": 1, "path": "standards/api-design.md"}).json()
    assert standard["summary"] == "How APIs look." and standard["rules"] == ["Plural nouns."]
    assert standard["content"].endswith("# API design\n\nUse plural nouns for collections.\nReturn problem details on errors.\n")
    agent = next(agent for agent in client.get("/api/v1/environments/1/agents").json() if agent["name"] == "planner")
    assert agent["preset"] == "Deep" and agent["description"] == "Plans work"


def test_hidden_characters_in_a_document_are_warned_about_and_shown(client):
    data = zip_of({"standards/safe.md": "# Safe\n\nNever log secrets\u200b.\n", "standards/clean.md": "# Clean\n\nText.\n"})
    proposals = {proposal["source"]: proposal for proposal in analyze(client, "docs.zip", data).json()}

    assert any("invisible character" in warning for warning in proposals["standards/safe.md"]["warnings"])
    assert "\u27e6U+200B ZERO WIDTH SPACE\u27e7" in proposals["standards/safe.md"]["body_marked"]
    assert proposals["standards/clean.md"]["warnings"] == []


def test_unsupported_uploads_are_refused(client):
    assert analyze(client, "photo.png", b"\x89PNG").status_code == 422
    duplicate = client.post(
        "/api/v1/blueprint/import/apply",
        params={"environment_id": 1},
        json={"items": [{"kind": "command", "name": "x", "body": "a"}, {"kind": "command", "name": "X", "body": "b"}]},
    )
    assert duplicate.status_code == 422
