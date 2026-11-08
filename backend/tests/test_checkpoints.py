"""Checkpoints: a snapshot before each action that can change files, its changes, and roll back."""

from pathlib import Path

from app.checkpoints.service import call_key
from app.checkpoints.service import CheckpointStore
from app.checkpoints.shadow import MAX_FILE_BYTES
from app.providers.fake_provider import ScriptedResponse
from tests.conftest import parse_events
from tests.test_delegation import project_conversation


def folder_of(client, project: dict) -> Path:
    return Path(client.app.state.workspace_dir) / project["folder"]


def checkpoints(client, project: dict, **params) -> list[dict]:
    response = client.get(f"/api/v1/projects/{project['id']}/checkpoints", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_each_changing_action_gets_a_checkpoint_that_can_be_rolled_back(client, fake):
    project, conversation_id = project_conversation(client)
    folder = folder_of(client, project)
    (folder / "node_modules").mkdir(parents=True, exist_ok=True)
    (folder / "node_modules" / "lib.js").write_text("kept", encoding="utf-8")
    write = {"path": "notes.txt", "content": "one\n"}
    edit = {"path": "notes.txt", "old_string": "one", "new_string": "two"}
    fake.responses.append(
        ScriptedResponse(text="Done.", tool_calls=(("write_file", write), ("edit_file", edit), ("read_file", {"path": "notes.txt"})))
    )
    response = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Write notes"})
    events = parse_events(response.text)
    assert [data["call_key"] for name, data in events if name == "checkpoint"] == [
        call_key("write_file", write),
        call_key("edit_file", edit),
    ]

    listed = checkpoints(client, project, conversation_id=conversation_id)
    assert [(item["tool"], item["kind"]) for item in listed] == [("edit_file", "action"), ("write_file", "action")]
    before_edit, before_write = listed
    assert before_write["conversation_title"] is not None

    # The changes of an action: up to the next checkpoint, and for the last one up to now.
    first = client.get(f"/api/v1/projects/{project['id']}/checkpoints/{before_write['id']}/changes").json()
    assert "+one" in first["diff"] and "two" not in first["diff"]
    last = client.get(f"/api/v1/projects/{project['id']}/checkpoints/{before_edit['id']}/changes").json()
    assert "-one" in last["diff"] and "+two" in last["diff"]

    # Roll back to before the write: the preview, then the file is gone and ignored folders stay.
    preview = client.get(f"/api/v1/projects/{project['id']}/checkpoints/{before_write['id']}/restore-preview").json()
    assert preview["paths"] == ["notes.txt"]
    restored = client.post(f"/api/v1/projects/{project['id']}/checkpoints/{before_write['id']}/restore")
    assert restored.status_code == 200, restored.text
    assert not (folder / "notes.txt").exists()
    assert (folder / "node_modules" / "lib.js").read_text(encoding="utf-8") == "kept"

    # The roll back is itself undone through the checkpoint it took first.
    before_id = restored.json()["before_id"]
    assert checkpoints(client, project)[0] == {**checkpoints(client, project)[0], "id": before_id, "kind": "restore"}
    client.post(f"/api/v1/projects/{project['id']}/checkpoints/{before_id}/restore")
    assert (folder / "notes.txt").read_text(encoding="utf-8") == "two\n"


def test_unchanged_state_shares_its_snapshot_and_large_files_are_left_out(client, tmp_path):
    project, _conversation_id = project_conversation(client)
    folder = folder_of(client, project)
    (folder / "big.bin").write_bytes(b"x" * (MAX_FILE_BYTES + 1))
    (folder / "small.txt").write_text("hi", encoding="utf-8")
    store = CheckpointStore(client.app.state.session_factory, Path(client.app.state.data_dir))
    first = store.take(project["id"], folder, "run_command", "Run: ls")
    second = store.take(project["id"], folder, "run_command", "Run: ls")
    assert first is not None and second is not None
    listed = {item["id"]: item for item in checkpoints(client, project)}
    assert set(listed) == {first, second}
    repository = store.repository(project["id"], folder)
    with client.app.state.session_factory() as session:
        from app.storage.models import Checkpoint

        one, two = session.get_one(Checkpoint, first), session.get_one(Checkpoint, second)
        assert one.commit == two.commit
    files = repository.git("ls-tree", "-r", "--name-only", one.commit).split()
    assert "small.txt" in files and "big.bin" not in files


def test_checkpoints_of_another_project_are_not_found(client):
    project, _ = project_conversation(client)
    other = client.post("/api/v1/environments/1/projects", json={"name": "Other"}).json()
    folder = folder_of(client, project)
    store = CheckpointStore(client.app.state.session_factory, Path(client.app.state.data_dir))
    checkpoint_id = store.take(project["id"], folder, "write_file", "write_file: a")
    assert client.get(f"/api/v1/projects/{other['id']}/checkpoints/{checkpoint_id}/changes").status_code == 404
