"""Memory suggestions: proposed by the utility model, accepted, edited or dismissed by the user."""

import json
import subprocess
from pathlib import Path

from sqlalchemy import select

from app.projects.suggestions import TURNS_BETWEEN, parse_proposals
from app.providers.fake_provider import ScriptedResponse
from app.storage.models import UsageRecord
from tests.test_chat import send
from tests.test_delegation import project_conversation


def answer(*items: tuple[str, str]) -> ScriptedResponse:
    return ScriptedResponse(text=json.dumps([{"section": section, "text": text} for section, text in items]))


def entries(client, project_id: int) -> list[str]:
    return [item["text"] for item in client.get(f"/api/v1/projects/{project_id}/memory/entries").json()["entries"]]


def test_the_filter_drops_temporary_and_malformed_items():
    text = json.dumps(
        [
            {"section": "Decisions", "text": "Prices are stored in cents as integers to avoid rounding."},
            {"section": "Notes", "text": "The dev server ran on localhost:5173 today."},
            {"section": "Lessons learned", "text": "The agent ran the tests twice before they passed."},
            {"section": "Odd", "text": "Use pnpm, not npm, for every package script in this repo."},
            {"section": "Notes", "text": "short"},
            "not an object",
        ]
    )
    proposals = parse_proposals(f"Here you go:\n{text}\nDone.")
    assert [(item.section, item.text[:10]) for item in proposals] == [("Decisions", "Prices are"), ("Notes", "Use pnpm, ")]
    assert parse_proposals("no json here") == []


def test_suggestions_on_demand_accept_edit_and_dismiss(client, fake):
    project, conversation_id = project_conversation(client)
    client.post(f"/api/v1/projects/{project['id']}/memory/entries", json={"section": "Conventions", "text": "Use pnpm for every package script."})
    fake.responses.append(ScriptedResponse(text="Noted."))
    send(client, conversation_id, "We store prices in cents. And use pnpm for all package scripts, always.")
    fake.responses.append(
        answer(
            ("Decisions", "Prices are stored in cents as integers to avoid rounding."),
            ("Conventions", "Use pnpm for all package scripts, always."),
            ("Notes", "The checkout page has a coupon field."),
        )
    )
    suggested = client.post(f"/api/v1/projects/{project['id']}/memory/suggestions", json={}).json()
    assert [item["updates_entry"] for item in suggested] == [None, "Use pnpm for every package script.", None]
    assert suggested[1]["section"] == "Conventions"
    prompt = "\n".join(block.get("text", "") for message in fake.requests[-1].messages for block in message.content)
    assert "We store prices in cents" in prompt and "Use pnpm for every package script." in prompt

    folder = Path(client.app.state.workspace_dir) / project["folder"]
    commits = lambda: int(subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=folder, capture_output=True, text=True).stdout)  # noqa: E731
    before = commits()
    accepted = client.post(f"/api/v1/projects/{project['id']}/memory/suggestions/{suggested[0]['id']}/accept", json={})
    assert accepted.status_code == 200 and commits() == before + 1
    # The update replaces the entry it matched.
    client.post(
        f"/api/v1/projects/{project['id']}/memory/suggestions/{suggested[1]['id']}/accept",
        json={"text": "Use pnpm for all package scripts; npm breaks the lockfile."},
    )
    texts = entries(client, project["id"])
    assert "Use pnpm for every package script." not in texts
    assert "Use pnpm for all package scripts; npm breaks the lockfile." in texts
    assert "Prices are stored in cents as integers to avoid rounding." in texts
    assert client.delete(f"/api/v1/projects/{project['id']}/memory/suggestions/{suggested[2]['id']}").status_code == 204
    assert client.get(f"/api/v1/projects/{project['id']}/memory/suggestions").json() == []
    assert client.delete(f"/api/v1/projects/{project['id']}/memory/suggestions/{suggested[2]['id']}").status_code == 404
    with client.app.state.session_factory() as session:
        assert session.scalar(select(UsageRecord).where(UsageRecord.task == "memory_suggestion")) is not None

    # Only the conversation since the last check is read.
    fake.responses.append(answer())
    client.post(f"/api/v1/projects/{project['id']}/memory/suggestions", json={})
    assert len(fake.requests) == 2  # Nothing new to read: no model call.


def test_suggestions_come_by_themselves_after_ten_turns(client, fake):
    client.app.state.memory_suggestions = True
    project, conversation_id = project_conversation(client)
    for number in range(TURNS_BETWEEN - 1):
        fake.responses.append(ScriptedResponse(text=f"Answer {number}."))
        send(client, conversation_id, f"Question {number}?")
    assert client.get(f"/api/v1/projects/{project['id']}/memory/suggestions").json() == []
    fake.responses.extend([ScriptedResponse(text="Last."), answer(("Lessons learned", "Run migrations before the seed script."))])
    send(client, conversation_id, "One more?")
    # The suggestion runs in the background; wait for it.
    import time

    for _ in range(100):
        if client.get(f"/api/v1/projects/{project['id']}/memory/suggestions").json():
            break
        time.sleep(0.05)
    assert [item["text"] for item in client.get(f"/api/v1/projects/{project['id']}/memory/suggestions").json()] == [
        "Run migrations before the seed script."
    ]


def test_adding_a_near_duplicate_by_hand_warns(client):
    project, _ = project_conversation(client)
    url = f"/api/v1/projects/{project['id']}/memory/entries"
    first = client.post(url, json={"section": "Decisions", "text": "Deploy only from the main branch."}).json()
    assert first["similar"] is None
    second = client.post(url, json={"section": "Decisions", "text": "Deploy only from the main branch, never others."}).json()
    assert second["similar"] == "Deploy only from the main branch."
