"""The shared document beside a conversation: the document panel and taking turns."""

import hashlib
from pathlib import Path

from app.agent.runs import RunConfig, RunServices
from app.agent.tools import tools_for
from app.documents.service import USER_CHANGE_NOTE, DocumentLink
from app.providers.fake_provider import ScriptedResponse
from app.storage.models import SharedDocument
from tests.test_chat import send, start_conversation
from tests.test_delegation import project_conversation
from tests.test_files import forget_connections, target_project  # noqa: F401  (a fixture)
from tests.test_ssh_targets import server  # noqa: F401  (the server fixture)
from tests.test_tool_guard import run_steps

NOTE_START = USER_CHANGE_NOTE.split("{path}")[0]


def folder_of(client, project: dict) -> Path:
    return Path(client.app.state.workspace_dir) / project["folder"]


def write(folder: Path, path: str, text: str) -> Path:
    file = folder / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(text.encode("utf-8"))
    return file


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def open_as_user(client, conversation_id: int, path: str):
    return client.put(f"/api/v1/conversations/{conversation_id}/document", json={"path": path})


def document(client, conversation_id: int) -> dict | None:
    response = client.get(f"/api/v1/conversations/{conversation_id}/document")
    assert response.status_code == 200, response.text
    return response.json()


def save(client, project: dict, path: str, text: str, base_hash: str | None):
    return client.put(
        f"/api/v1/projects/{project['id']}/files/content", json={"path": path, "text": text, "base_hash": base_hash}
    )


def start_document_run(client, project: dict, conversation_id: int):
    """A running reply of the conversation, as launch_reply starts it, to call tools on directly."""
    registry = client.app.state.runs
    run = registry.start(RunConfig(
        conversation_id=conversation_id,
        environment_id=1,
        project_id=project["id"],
        project_folder=folder_of(client, project),
        blueprint_layers=[],
        tool_names=tools_for(has_project=True, has_attachments=False),
    ), RunServices(documents=DocumentLink(client.app.state.session_factory, conversation_id)))
    return registry, run


# --- 34.1 Document panel -------------------------------------------------------------------------------------------


def test_the_user_opens_a_markdown_file_as_the_document_and_closes_it(client):
    project, conversation_id = project_conversation(client)
    write(folder_of(client, project), "docs/plan.md", "# Plan\n")
    assert document(client, conversation_id) is None
    response = open_as_user(client, conversation_id, "./docs/plan.md")
    assert response.status_code == 200, response.text
    shown = response.json()
    assert shown == {
        "path": "docs/plan.md",
        "name": "plan.md",
        "opened_by": "user",
        "exists": True,
        "hash": digest("# Plan\n"),
        "review": None,
    }
    assert document(client, conversation_id)["path"] == "docs/plan.md"
    # Another document takes its place.
    write(folder_of(client, project), "README.md", "# Shop\n")
    assert open_as_user(client, conversation_id, "README.md").json()["path"] == "README.md"
    assert client.delete(f"/api/v1/conversations/{conversation_id}/document").status_code == 204
    assert document(client, conversation_id) is None


def test_only_markdown_inside_the_project_opens_and_only_in_a_project(client):
    project, conversation_id = project_conversation(client)
    write(folder_of(client, project), "app.py", "x = 1\n")
    assert open_as_user(client, conversation_id, "app.py").status_code == 422
    assert open_as_user(client, conversation_id, "../../outside.md").status_code == 422
    assert open_as_user(client, conversation_id, ".git/notes.md").status_code == 422
    outside = start_conversation(client)
    assert open_as_user(client, outside, "notes.md").status_code == 409
    assert document(client, outside) is None


def test_the_agent_opens_a_document_and_is_told_about_it_in_the_next_reply(client, fake):
    project, conversation_id = project_conversation(client)
    write(folder_of(client, project), "notes.md", "# Notes\n")
    fake.responses.extend(
        [
            ScriptedResponse(text="It is open.", tool_calls=(("open_document", {"path": "notes.md"}),)),
            ScriptedResponse(text="Fine."),
        ]
    )
    events = send(client, conversation_id, "Open the notes")
    assert [data["path"] for name, data in events if name == "document_open"] == ["notes.md"]
    result = next(data for name, data in events if name == "tool_result")
    assert not result["is_error"] and "beside the chat" in result["output"]
    assert document(client, conversation_id)["opened_by"] == "agent"
    send(client, conversation_id, "Thanks")
    system = " ".join(part.text for part in fake.requests[-1].system)
    assert "Shared document: notes.md is open" in system


def test_open_document_refuses_other_files_and_needs_a_project(client, fake):
    project, conversation_id = project_conversation(client)
    write(folder_of(client, project), "app.py", "x = 1\n")
    fake.responses.append(ScriptedResponse(text="No.", tool_calls=(("open_document", {"path": "app.py"}),)))
    events = send(client, conversation_id, "Open app.py")
    result = next(data for name, data in events if name == "tool_result")
    assert result["is_error"] and "Markdown" in result["output"]
    assert "open_document" not in tools_for(has_project=False, has_attachments=False)


def test_a_document_goes_with_its_conversation(client):
    project, conversation_id = project_conversation(client)
    write(folder_of(client, project), "notes.md", "# Notes\n")
    open_as_user(client, conversation_id, "notes.md")
    assert client.delete(f"/api/v1/conversations/{conversation_id}").status_code == 204
    with client.app.state.session_factory() as session:
        assert session.query(SharedDocument).count() == 0


# --- 34.2 Taking turns ---------------------------------------------------------------------------------------------


def test_the_agents_write_shows_as_a_diff_and_can_be_rolled_back(client, fake):
    project, conversation_id = project_conversation(client)
    file = write(folder_of(client, project), "notes.md", "# Notes\r\n\r\nOne idea.\r\n")
    open_as_user(client, conversation_id, "notes.md")
    fake.responses.append(
        ScriptedResponse(
            text="Added one.",
            tool_calls=(
                ("read_file", {"path": "notes.md"}),
                ("edit_file", {"path": "notes.md", "old_string": "One idea.", "new_string": "One idea.\nTwo ideas."}),
            ),
        )
    )
    events = send(client, conversation_id, "Add an idea")
    names = [name for name, _ in events]
    # The panel locks before the write and follows it after.
    assert names.index("document_writing") < names.index("document_change")
    change = next(data for name, data in events if name == "document_change")
    after = file.read_bytes()
    assert change == {"event": "document_change", "path": "notes.md", "hash": hashlib.sha256(after).hexdigest()}
    shown = document(client, conversation_id)
    review = shown["review"]
    assert " One idea." in review["diff"] and "+Two ideas." in review["diff"]
    assert review["created"] is False and review["checkpoint_id"]
    # The checkpoint before the agent's change belongs to its edit_file call.
    listed = client.get(f"/api/v1/projects/{project['id']}/checkpoints").json()
    assert next(item for item in listed if item["id"] == review["checkpoint_id"])["tool"] == "edit_file"

    rolled = client.post(f"/api/v1/conversations/{conversation_id}/document/rollback", json={"hash": shown["hash"]})
    assert rolled.status_code == 200, rolled.text
    assert file.read_bytes() == b"# Notes\r\n\r\nOne idea.\r\n" and rolled.json()["review"] is None
    # The roll back can itself be undone: a checkpoint holds the agent's version.
    latest = client.get(f"/api/v1/projects/{project['id']}/checkpoints").json()[0]
    assert latest["kind"] == "restore" and "Before rolling back" in latest["summary"]

    # The agent learns of the roll back with its next tool result.
    fake.responses.append(ScriptedResponse(text="Seen.", tool_calls=(("list_files", {}),)))
    events = send(client, conversation_id, "Go on")
    result = next(data for name, data in events if name == "tool_result")
    assert f"{NOTE_START}notes.md" in result["output"] and "-Two ideas." in result["output"]


def test_accepting_keeps_the_change_and_needs_the_version_that_was_seen(client, fake):
    project, conversation_id = project_conversation(client)
    file = write(folder_of(client, project), "notes.md", "# Notes\n")
    open_as_user(client, conversation_id, "notes.md")
    fake.responses.append(
        ScriptedResponse(text="Done.", tool_calls=(("write_file", {"path": "notes.md", "content": "# Notes\n\nMore.\n"}),))
    )
    send(client, conversation_id, "Write more")
    shown = document(client, conversation_id)
    assert shown["review"] is not None
    stale = client.post(f"/api/v1/conversations/{conversation_id}/document/accept", json={"hash": digest("# Notes\n")})
    assert stale.status_code == 409
    accepted = client.post(f"/api/v1/conversations/{conversation_id}/document/accept", json={"hash": shown["hash"]})
    assert accepted.status_code == 200 and accepted.json()["review"] is None
    assert file.read_text() == "# Notes\n\nMore.\n"
    again = client.post(f"/api/v1/conversations/{conversation_id}/document/rollback", json={"hash": shown["hash"]})
    assert again.status_code == 409


def test_rolling_back_a_document_the_agent_created_removes_it(client, fake):
    project, conversation_id = project_conversation(client)
    fake.responses.append(
        ScriptedResponse(
            text="Started it.",
            tool_calls=(
                ("open_document", {"path": "docs/draft.md"}),
                ("write_file", {"path": "docs/draft.md", "content": "# Draft\n"}),
            ),
        )
    )
    send(client, conversation_id, "Start a draft")
    shown = document(client, conversation_id)
    assert shown["exists"] and shown["review"]["created"] is True and "+# Draft" in shown["review"]["diff"]
    rolled = client.post(f"/api/v1/conversations/{conversation_id}/document/rollback", json={"hash": shown["hash"]})
    assert rolled.status_code == 200, rolled.text
    assert not (folder_of(client, project) / "docs" / "draft.md").exists() and rolled.json()["exists"] is False


def test_a_change_back_to_the_start_leaves_nothing_to_review(client, fake):
    project, conversation_id = project_conversation(client)
    write(folder_of(client, project), "notes.md", "# Notes\n")
    open_as_user(client, conversation_id, "notes.md")
    fake.responses.append(
        ScriptedResponse(
            text="Undone.",
            tool_calls=(
                ("write_file", {"path": "notes.md", "content": "# Other\n"}),
                ("write_file", {"path": "notes.md", "content": "# Notes\n"}),
            ),
        )
    )
    send(client, conversation_id, "Try something")
    assert document(client, conversation_id)["review"] is None


def test_a_user_edit_during_a_reply_reaches_the_agent_with_the_next_tool_result(client):
    project, conversation_id = project_conversation(client)
    write(folder_of(client, project), "notes.md", "# Notes\n\nFirst.\n")
    opened = open_as_user(client, conversation_id, "notes.md").json()
    registry, run = start_document_run(client, project, conversation_id)
    try:
        results, _ = run_steps(registry, run, [("read_file", {"path": "notes.md"})])
        assert NOTE_START not in results[0][0]
        # The user saves in the panel while the reply works.
        saved = save(client, project, "notes.md", "# Notes\n\nFirst.\nFrom the user.\n", opened["hash"])
        assert saved.status_code == 200, saved.text
        assert run.documents.unchecked is True
        results, _ = run_steps(registry, run, [("list_files", {}), ("list_files", {"path": "."})])
        first, second = results[0][0], results[1][0]
        assert f"{NOTE_START}notes.md" in first and "+From the user." in first
        # Delivered once.
        assert NOTE_START not in second
    finally:
        registry.finish(run)


def test_a_user_edit_between_replies_reaches_the_agent_in_the_next_reply(client, fake):
    project, conversation_id = project_conversation(client)
    write(folder_of(client, project), "notes.md", "# Notes\n")
    opened = open_as_user(client, conversation_id, "notes.md").json()
    save(client, project, "notes.md", "# Notes\n\nA thought.\n", opened["hash"])
    fake.responses.append(ScriptedResponse(text="Seen.", tool_calls=(("git_status", {}),)))
    events = send(client, conversation_id, "Look")
    result = next(data for name, data in events if name == "tool_result")
    assert f"{NOTE_START}notes.md" in result["output"] and "+A thought." in result["output"]
    assert [data["path"] for name, data in events if name == "document_seen"] == ["notes.md"]


def test_the_agent_cannot_write_over_a_user_change_it_has_not_seen(client):
    project, conversation_id = project_conversation(client)
    file = write(folder_of(client, project), "notes.md", "# Notes\n")
    opened = open_as_user(client, conversation_id, "notes.md").json()
    registry, run = start_document_run(client, project, conversation_id)
    try:
        run_steps(registry, run, [("read_file", {"path": "notes.md"})])
        save(client, project, "notes.md", "# Notes\n\nMine.\n", opened["hash"])
        results, _ = run_steps(registry, run, [("write_file", {"path": "notes.md", "content": "# Replaced\n"})])
        output, is_error = results[0]
        assert is_error and "the user changed it since you last saw it" in output and "+Mine." in output
        assert file.read_text() == "# Notes\n\nMine.\n"
        # Now that it has seen the change, the agent may build on it.
        results, _ = run_steps(
            registry, run, [("edit_file", {"path": "notes.md", "old_string": "Mine.", "new_string": "Mine. Yours."})]
        )
        assert not results[0][1] and file.read_text() == "# Notes\n\nMine. Yours.\n"
    finally:
        registry.finish(run)


def test_the_user_cannot_save_over_an_agent_change_they_have_not_seen(client, fake):
    project, conversation_id = project_conversation(client)
    file = write(folder_of(client, project), "notes.md", "# Notes\n")
    opened = open_as_user(client, conversation_id, "notes.md").json()
    fake.responses.append(
        ScriptedResponse(text="Done.", tool_calls=(("write_file", {"path": "notes.md", "content": "# By YAAP\n"}),))
    )
    send(client, conversation_id, "Rewrite it")
    # The panel still has the version from before the agent's change.
    refused = save(client, project, "notes.md", "# By the user\n", opened["hash"])
    assert refused.status_code == 409 and file.read_text() == "# By YAAP\n"
    # A roll back of a version the user did not see is refused as well.
    stale = client.post(f"/api/v1/conversations/{conversation_id}/document/rollback", json={"hash": opened["hash"]})
    assert stale.status_code == 409 and file.read_text() == "# By YAAP\n"


def test_a_roll_back_on_a_computer_over_ssh_writes_or_removes_the_file(client, server, forget_connections):  # noqa: F811
    project = target_project(client, server)
    conversation_id = client.post(
        "/api/v1/environments/1/conversations",
        json={"model": "claude-opus-5-5", "effort": "medium", "project_id": project["id"]},
    ).json()["id"]
    remote = server.root / "work" / "site"
    write(remote, "notes.md", "# By YAAP\n")
    shown = open_as_user(client, conversation_id, "notes.md").json()
    # As if the agent's turn changed it there (a sub-run writes the same way).
    with client.app.state.session_factory() as session:
        row = session.query(SharedDocument).one()
        row.review_hash, row.review_base, row.review_created = shown["hash"], "# Before\n", False
        session.commit()
    review = document(client, conversation_id)["review"]
    assert review["checkpoint_id"] is None and "-# Before" in review["diff"]
    rolled = client.post(f"/api/v1/conversations/{conversation_id}/document/rollback", json={"hash": shown["hash"]})
    assert rolled.status_code == 200, rolled.text
    assert (remote / "notes.md").read_text() == "# Before\n"
    with client.app.state.session_factory() as session:
        row = session.query(SharedDocument).one()
        row.review_hash, row.review_base, row.review_created = digest("# Before\n"), None, True
        session.commit()
    rolled = client.post(f"/api/v1/conversations/{conversation_id}/document/rollback", json={"hash": digest("# Before\n")})
    assert rolled.status_code == 200, rolled.text
    assert not (remote / "notes.md").exists()


def test_a_sub_runs_change_of_the_document_waits_for_review_too(client, fake):
    project, conversation_id = project_conversation(client)
    write(folder_of(client, project), "notes.md", "# Notes\n")
    open_as_user(client, conversation_id, "notes.md")
    fake.responses.extend(
        [
            ScriptedResponse(text="The worker wrote it.", tool_calls=(("delegate", {"task": "Extend notes.md"}),)),
            ScriptedResponse(
                text="Extended.", tool_calls=(("write_file", {"path": "notes.md", "content": "# Notes\n\nMore.\n"}),)
            ),
        ]
    )
    events = send(client, conversation_id, "Have the worker extend the notes")
    assert any(name == "document_change" for name, _ in events)
    assert "+More." in document(client, conversation_id)["review"]["diff"]
