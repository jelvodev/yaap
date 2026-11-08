"""Memory dashboard and recall: entries one at a time, and what of a large memory goes into the prompt."""

from pathlib import Path

from app.git.service import run_git
from app.projects.memory import RECALL_TOKENS, TEMPLATE, add_entry, parse_entries, recall
from app.providers.fake_provider import ScriptedResponse
from tests.test_delegation import project_conversation


def large_memory(count: int = 300) -> str:
    text = TEMPLATE
    for number in range(count):
        text = add_entry(text, "Decisions", f"Decision {number}: the widget pipeline uses option {number} for reasons.")
    text = add_entry(text, "Conventions", "Invoices are numbered per calendar year.")
    text = add_entry(text, "Notes", "The staging server reboots on Sundays.")
    return text


def test_entries_are_parsed_per_section_with_continuations():
    text = "# Project memory\n\nLoose line.\n\n## Decisions\n\n- One\n  continued here\n* Two\n\n## Odd\n\nThree\n"
    entries = parse_entries(text)
    assert [(entry.section, entry.text) for entry in entries] == [
        ("", "Loose line."),
        ("Decisions", "One continued here"),
        ("Decisions", "Two"),
        ("Odd", "Three"),
    ]
    assert len({entry.id for entry in entries}) == 4


def test_entries_are_added_edited_and_forgotten_one_commit_each(client):
    project = client.post("/api/v1/environments/1/projects", json={"name": "Site"}).json()
    folder = Path(client.app.state.workspace_dir) / project["folder"]
    base = f"/api/v1/projects/{project['id']}/memory/entries"

    empty = client.get(base).json()
    assert empty["entries"] == [] and empty["sections"][:4] == ["Decisions", "Conventions", "Lessons learned", "Notes"]

    client.post(base, json={"section": "Decisions", "text": "Use SQLite."})
    added = client.post(base, json={"section": "Notes", "text": "Ask before deploying."}).json()
    assert [(entry["section"], entry["text"]) for entry in added["entries"]] == [
        ("Decisions", "Use SQLite."),
        ("Notes", "Ask before deploying."),
    ]
    assert client.post(base, json={"section": "Gossip", "text": "x"}).status_code == 422

    first = added["entries"][0]
    edited = client.put(f"{base}/{first['id']}", json={"text": "Use SQLite with WAL."}).json()
    assert edited["entries"][0]["text"] == "Use SQLite with WAL."
    # The old id no longer matches the text: the memory changed in the meantime.
    stale = client.put(f"{base}/{first['id']}", json={"text": "Something else."})
    assert stale.status_code == 409

    forgotten = client.delete(f"{base}/{edited['entries'][1]['id']}").json()
    assert [entry["text"] for entry in forgotten["entries"]] == ["Use SQLite with WAL."]
    content = client.get(f"/api/v1/projects/{project['id']}/memory").json()["content"]
    assert "## Notes" in content and "Ask before deploying" not in content

    log = run_git(["log", "--format=%s"], folder).stdout.splitlines()
    assert log[:4] == [
        "Forget: Ask before deploying.",
        "Edit memory: Use SQLite with WAL.",
        "Remember: Ask before deploying.",
        "Remember: Use SQLite.",
    ]


def test_a_small_memory_is_recalled_whole_a_large_one_in_part():
    small = add_entry(TEMPLATE, "Decisions", "Use SQLite.")
    assert recall(small, "anything").whole

    text = large_memory()
    assert len(text) // 4 > RECALL_TOKENS
    result = recall(text, "When does the staging server reboot?")
    assert not result.whole and result.total == 302
    assert "Decision 299:" in result.text and "Decision 297:" in result.text  # The newest of the section.
    assert "Decision 10:" not in result.text
    assert "Invoices are numbered" in result.text  # A section's newest, however few it has.
    assert "The staging server reboots on Sundays." in result.text
    assert "more entries not shown" in result.text and "search_project" in result.text
    assert len(result.text) // 4 < RECALL_TOKENS

    # Entries in the memory chunks found by meaning count as related, before word matches.
    chunk = "## Decisions\n\n- Decision 42: the widget pipeline uses option 42 for reasons.\n"
    related = recall(text, "no shared words", [chunk])
    assert [entry.text for entry in related.related][0].startswith("Decision 42:")
    assert "Decision 42:" in related.text


def test_the_prompt_and_the_context_breakdown_show_the_recalled_entries(client, fake):
    project, conversation_id = project_conversation(client)
    client.put(f"/api/v1/projects/{project['id']}/memory", json={"content": large_memory()})
    fake.responses.append(ScriptedResponse(text="On Sundays."))

    client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "When does staging reboot?"})

    system = "\n\n".join(part.text for part in fake.requests[0].system)
    # Part 5 only says the memory is large, so it stays the same between messages; the entries come with the message.
    assert "larger than fits here" in system and "Decision 299:" not in system
    message = " ".join(block.get("text", "") for block in fake.requests[0].messages[-1].content)
    assert "The staging server reboots on Sundays." in message
    assert "Decision 10:" not in message and "Decision 299:" in message

    breakdown = client.get(f"/api/v1/conversations/{conversation_id}/context").json()
    memory = breakdown["memory"]
    assert memory["whole"] is False and memory["total_entries"] == 302
    reasons = {entry["text"]: entry["reason"] for entry in memory["entries"]}
    assert reasons["The staging server reboots on Sundays."] == "recent"
    assert any(reason == "recent" for reason in reasons.values())
