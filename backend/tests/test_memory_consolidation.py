"""Memory consolidation: the utility model proposes merges; the user accepts or rejects each."""

import json
import subprocess
import time
from pathlib import Path

from sqlalchemy import select

from app.projects.consolidation import TIDY_EVERY, parse_merges
from app.providers.fake_provider import ScriptedResponse
from app.storage.models import UsageRecord
from tests.test_chat import send
from tests.test_delegation import project_conversation


def merges(*items: tuple[list[int], str]) -> ScriptedResponse:
    return ScriptedResponse(text=json.dumps([{"entries": numbers, "text": text} for numbers, text in items]))


def add(client, project_id: int, section: str, text: str) -> None:
    response = client.post(f"/api/v1/projects/{project_id}/memory/entries", json={"section": section, "text": text})
    assert response.status_code == 200, response.text


def entries(client, project_id: int) -> list[tuple[str, str]]:
    found = client.get(f"/api/v1/projects/{project_id}/memory/entries").json()["entries"]
    return [(item["section"], item["text"]) for item in found]


def folder_of(client, project: dict) -> Path:
    return Path(client.app.state.workspace_dir) / project["folder"]


def commits(folder: Path) -> int:
    return int(subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=folder, capture_output=True, text=True).stdout)


def memory_project(client) -> dict:
    project, _ = project_conversation(client)
    add(client, project["id"], "Conventions", "Use pnpm for every package script.")
    add(client, project["id"], "Decisions", "Prices are stored in cents.")
    add(client, project["id"], "Conventions", "Package scripts run with pnpm, never npm.")
    add(client, project["id"], "Decisions", "Money amounts are integers in cents, to avoid rounding errors.")
    add(client, project["id"], "Notes", "The checkout page has a coupon field.")
    return project


def test_the_filter_drops_malformed_and_overlapping_merges():
    answer = json.dumps(
        [
            {"entries": [1, 3], "text": "Use pnpm for every package script, never npm."},
            {"entries": [3, 4], "text": "Entry three is already part of the first merge."},
            {"entries": [2], "text": "A merge needs at least two entries to replace."},
            {"entries": [2, 9], "text": "Nine is not one of the entries in the list."},
            {"entries": [2, 4], "text": "short"},
            {"entries": ["2", "5"], "text": "Numbers as strings are accepted when they are digits."},
            {"entries": [True, 2], "text": "A boolean is not an entry number, even though it is an int."},
            "not an object",
        ]
    )
    proposals = parse_merges(f"Here:\n{answer}\nDone.", 5)
    assert [(item.indexes, item.text[:10]) for item in proposals] == [((0, 2), "Use pnpm f"), ((1, 4), "Numbers as")]
    assert parse_merges("nothing to merge", 5) == []


def test_proposals_change_nothing_until_accepted_and_a_merge_is_one_commit(client, fake):
    project = memory_project(client)
    folder = folder_of(client, project)
    memory_file = folder / ".blueprint" / "memory.md"
    before_text, before_commits = memory_file.read_text(encoding="utf-8"), commits(folder)

    fake.responses.append(
        merges(
            ([3, 4], "Use pnpm for every package script, never npm."),
            ([1, 2], "Money amounts are stored as integers in cents, to avoid rounding errors."),
        )
    )
    proposed = client.post(f"/api/v1/projects/{project['id']}/memory/tidy").json()
    assert [[entry["text"] for entry in item["entries"]] for item in proposed] == [
        ["Use pnpm for every package script.", "Package scripts run with pnpm, never npm."],
        ["Prices are stored in cents.", "Money amounts are integers in cents, to avoid rounding errors."],
    ]
    assert [item["section"] for item in proposed] == ["Conventions", "Decisions"]
    prompt = "\n".join(block.get("text", "") for message in fake.requests[-1].messages for block in message.content)
    assert "3. [Conventions] Use pnpm for every package script." in prompt
    # A proposal is only a proposal: the memory file and its history are untouched.
    assert memory_file.read_text(encoding="utf-8") == before_text and commits(folder) == before_commits
    assert client.get(f"/api/v1/projects/{project['id']}/memory/merges").json() == proposed

    accepted = client.post(f"/api/v1/projects/{project['id']}/memory/merges/{proposed[0]['id']}/accept", json={})
    assert accepted.status_code == 200
    assert commits(folder) == before_commits + 1
    log = subprocess.run(["git", "log", "-1", "--name-only", "--format=%s"], cwd=folder, capture_output=True, text=True).stdout
    assert log.startswith("Merge 2 memory entries: Use pnpm") and ".blueprint/memory.md" in log
    assert entries(client, project["id"]) == [
        ("Decisions", "Prices are stored in cents."),
        ("Decisions", "Money amounts are integers in cents, to avoid rounding errors."),
        ("Conventions", "Use pnpm for every package script, never npm."),
        ("Notes", "The checkout page has a coupon field."),
    ]

    # Rejecting keeps the entries as they are.
    assert client.delete(f"/api/v1/projects/{project['id']}/memory/merges/{proposed[1]['id']}").status_code == 204
    assert commits(folder) == before_commits + 1
    assert len(entries(client, project["id"])) == 4
    assert client.get(f"/api/v1/projects/{project['id']}/memory/merges").json() == []
    assert client.post(f"/api/v1/projects/{project['id']}/memory/merges/{proposed[1]['id']}/accept", json={}).status_code == 404
    with client.app.state.session_factory() as session:
        assert session.scalar(select(UsageRecord).where(UsageRecord.task == "memory_tidy")) is not None


def test_an_edited_merge_and_a_merge_the_memory_overtook(client, fake):
    project = memory_project(client)
    fake.responses.append(
        merges(([3, 4], "Use pnpm for package scripts."), ([1, 2], "Money amounts are integers in cents."))
    )
    proposed = client.post(f"/api/v1/projects/{project['id']}/memory/tidy").json()
    edited = client.post(
        f"/api/v1/projects/{project['id']}/memory/merges/{proposed[0]['id']}/accept",
        json={"text": "Use pnpm for every package script; npm breaks the lockfile."},
    )
    assert ("Conventions", "Use pnpm for every package script; npm breaks the lockfile.") in entries(client, project["id"])

    # An entry of the second merge changes: the merge no longer fits and is dropped, not applied.
    entry = next(item for item in edited.json()["entries"] if item["text"] == "Prices are stored in cents.")
    client.put(f"/api/v1/projects/{project['id']}/memory/entries/{entry['id']}", json={"text": "Prices are in euro cents."})
    folder = folder_of(client, project)
    before = commits(folder)
    refused = client.post(f"/api/v1/projects/{project['id']}/memory/merges/{proposed[1]['id']}/accept", json={})
    assert refused.status_code == 409 and commits(folder) == before
    assert client.get(f"/api/v1/projects/{project['id']}/memory/merges").json() == []

    # Entries waiting in an open merge are not offered again.
    fake.responses.append(merges(([1, 2], "Prices are in euro cents and amounts are integers.")))
    client.post(f"/api/v1/projects/{project['id']}/memory/tidy")
    fake.responses.append(merges())
    client.post(f"/api/v1/projects/{project['id']}/memory/tidy")
    prompt = "\n".join(block.get("text", "") for message in fake.requests[-1].messages for block in message.content)
    assert "Prices are in euro cents." not in prompt and "coupon" in prompt


def test_tidy_up_comes_by_itself_after_twenty_new_entries(client, fake):
    client.app.state.memory_tidy = True
    project, _ = project_conversation(client)
    for number in range(TIDY_EVERY - 1):
        add(client, project["id"], "Notes", f"Fact number {number} about the shop's setup.")
    assert fake.requests == []
    fake.responses.append(merges(([1, 2], "Facts zero and one about the shop's setup, together.")))
    add(client, project["id"], "Notes", "Fact number 19 about the shop's setup.")
    proposed = client.get(f"/api/v1/projects/{project['id']}/memory/merges").json()
    assert [item["text"] for item in proposed] == ["Facts zero and one about the shop's setup, together."]
    # The next tidy-up waits for twenty more.
    add(client, project["id"], "Notes", "Fact number 20 about the shop's setup.")
    assert len(fake.requests) == 1


def test_tidy_up_without_a_utility_model_says_so(client):
    project = client.post("/api/v1/environments/1/projects", json={"name": "Shop"}).json()
    add(client, project["id"], "Notes", "One entry about the shop.")
    add(client, project["id"], "Notes", "Another entry about the shop.")
    response = client.post(f"/api/v1/projects/{project['id']}/memory/tidy")
    assert response.status_code == 503


def test_tidy_up_also_comes_after_a_reply(client, fake):
    project, conversation_id = project_conversation(client)
    for number in range(TIDY_EVERY):
        add(client, project["id"], "Notes", f"Fact number {number} about the shop's setup.")
    client.app.state.memory_tidy = True
    fake.responses.extend([ScriptedResponse(text="Noted."), merges(([1, 2], "Facts zero and one about the setup, together."))])
    send(client, conversation_id, "Anything else?")
    # The check runs in the background; wait for it.
    for _ in range(100):
        if client.get(f"/api/v1/projects/{project['id']}/memory/merges").json():
            break
        time.sleep(0.05)
    assert [item["text"] for item in client.get(f"/api/v1/projects/{project['id']}/memory/merges").json()] == [
        "Facts zero and one about the setup, together."
    ]
