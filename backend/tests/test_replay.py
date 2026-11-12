"""Replay: a reply replayed from one of its model calls, and an eval case made from the replay."""

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, update

from app.checkpoints.pins import case_owner
from app.checkpoints.service import CheckpointStore
from app.core.shutdown import stop_replies_and_their_work
from app.evals.cases import parse_case
from app.evals.play import checkpoint_tree
from app.evals.workspace import make_workspace
from app.git.service import run_git
from app.providers.base import HistoryMessage, TokenUsage
from app.providers.fake_provider import ScriptedResponse
from app.replays.history import LEFT_OUT_NOTE, last_user_text, model_history, snapshot_messages
from app.replays.service import workspace_folder
from app.storage.models import Checkpoint, CheckpointPin, Conversation, Environment, Replay
from app.tracing.snapshots import LEFT_OUT, SnapshotStore, trace_dir
from tests.test_eval_runs import fixture_project

WRITE_HELLO = ScriptedResponse(
    text="Written.",
    tool_calls=(("write_file", {"path": "hello.txt", "content": "hello"}),),
    call_usages=(TokenUsage(input_tokens=10, output_tokens=5), TokenUsage(input_tokens=20, output_tokens=3)),
)
WRITE_OTHER = ScriptedResponse(text="Done.", tool_calls=(("write_file", {"path": "other.txt", "content": "other"}),))
REPLAYED = ScriptedResponse(
    text="Replayed.",
    tool_calls=(
        ("read_file", {"path": "hello.txt"}),
        ("read_file", {"path": "other.txt"}),
        ("run_command", {"command": "echo hi"}),
    ),
)


def fixture_run(client, fake) -> tuple[dict, Path, int, dict]:
    """A project conversation whose first reply has three steps (model call, write_file, model call); a second reply
    then writes another file, after step 3."""
    project, folder, _commit = fixture_project(client)
    body = {"model": "claude-opus-5-5", "effort": "medium", "project_id": project["id"], "preset": "Balanced"}
    conversation_id = client.post("/api/v1/environments/1/conversations", json=body).json()["id"]
    fake.responses.extend([WRITE_HELLO, WRITE_OTHER])
    for text in ("Write hello", "Write other"):
        response = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": text})
        assert response.status_code == 200, response.text
    messages = client.get(f"/api/v1/conversations/{conversation_id}").json()["messages"]
    first_reply = next(message for message in messages if message["role"] == "assistant")
    trace = client.get(f"/api/v1/conversations/{conversation_id}/messages/{first_reply['id']}/trace").json()
    return project, folder, conversation_id, trace


def step_at(trace: dict, position: int) -> dict:
    return next(step for step in trace["run"]["steps"] if step["position"] == position)


def finished_replay(client, conversation_id: int, replay_id: int) -> dict:
    for _ in range(400):
        detail = client.get(f"/api/v1/conversations/{conversation_id}/replays/{replay_id}").json()
        if detail["replay"]["status"] not in ("queued", "running"):
            return detail
        time.sleep(0.05)
    raise AssertionError("the replay did not finish")


def folder_state(folder: Path) -> dict[str, str]:
    return {
        path.relative_to(folder).as_posix(): path.read_text(encoding="utf-8", errors="replace")
        for path in sorted(folder.rglob("*"))
        if path.is_file() and ".git" not in path.relative_to(folder).parts
    }


def start_replay(client, conversation_id: int, step_id: int, **variant) -> dict:
    response = client.post(f"/api/v1/conversations/{conversation_id}/steps/{step_id}/replays", json=variant)
    assert response.status_code == 202, response.text
    return response.json()


def test_a_replay_from_step_3_starts_with_its_messages_and_files_and_leaves_the_original(client, fake):
    project, folder, conversation_id, trace = fixture_run(client, fake)
    step = step_at(trace, 3)
    assert step["kind"] == "model" and [s["kind"] for s in trace["run"]["steps"]] == ["model", "tool", "model"]
    before_trace = client.get(f"/api/v1/conversations/{conversation_id}/runs/{trace['run']['id']}/trace").json()
    before_conversation = client.get(f"/api/v1/conversations/{conversation_id}").json()
    before_files, before_head = folder_state(folder), run_git(["rev-parse", "HEAD"], folder).stdout
    fake.responses.append(REPLAYED)

    started = start_replay(client, conversation_id, step["id"], preset="Balanced")
    detail = finished_replay(client, conversation_id, started["id"])

    replay = detail["replay"]
    assert replay["status"] == "done", replay
    assert replay["step_position"] == 3 and replay["variant"]["label"] == "preset Balanced"
    # The same messages as step 3: the first message with its turn context, then what the reply had done.
    store = SnapshotStore(trace_dir(client.app.state.data_dir))
    with client.app.state.session_factory() as session:
        digest = session.get_one(Replay, replay["id"]).snapshot
    original = snapshot_messages(store.load(digest))
    assert [message.role for message in original] == ["user", "assistant"]
    assert last_user_text(original) == "Write hello"
    sent = fake.requests[-1].messages
    assert sent == model_history(original)
    assert [message.role for message in sent] == ["user", "assistant", "user"]
    assert "[Called the tool write_file" in sent[1].content[0]["text"]
    # The files of step 3: hello.txt written by step 2, other.txt (written after step 3) not yet there.
    calls = [
        block
        for message in client.get(f"/api/v1/conversations/{replay['conversation_id']}").json()["messages"]
        for block in message["content"]
        if block["type"] == "tool_call"
    ]
    hello, other, command = calls
    assert "hello" in hello["output"] and not hello["is_error"]
    assert other["is_error"]
    # Unattended: the command that would ask was refused and counted.
    assert command["output"].startswith("Refused:") and replay["asked"] == 1
    # The replay's trace, shown beside the original's, carries the refused call as asked.
    replay_trace = client.get(f"/api/v1/conversations/{replay['conversation_id']}/runs/{replay['agent_run_id']}/trace")
    assert replay_trace.status_code == 200, replay_trace.text
    assert [step["approval"] for step in replay_trace.json()["run"]["steps"]][-1] == "asked"
    # Beside the original: the original from step 3 on, the replay as a whole.
    assert detail["original"]["steps"] == 1 and detail["original"]["final_answer"] == "Written."
    assert detail["replayed"]["steps"] == 4 and detail["replayed"]["final_answer"] == "Replayed."
    assert detail["replayed"]["tokens"] == 15
    # A separate, archived conversation, linked from the original; its workspace is gone.
    listed = client.get(f"/api/v1/conversations/{conversation_id}/replays").json()
    assert [item["id"] for item in listed] == [replay["id"]]
    with client.app.state.session_factory() as session:
        assert session.get_one(Conversation, replay["conversation_id"]).archived_at is not None
        slug = session.get_one(Environment, session.get_one(Replay, replay["id"]).environment_id).slug
    assert not workspace_folder(client.app.state.workspace_dir, slug, replay["id"]).exists()
    # The original run, its conversation and the project folder are as they were.
    after_trace = client.get(f"/api/v1/conversations/{conversation_id}/runs/{trace['run']['id']}/trace").json()
    assert after_trace == before_trace
    assert client.get(f"/api/v1/conversations/{conversation_id}").json() == before_conversation
    assert folder_state(folder) == before_files and run_git(["rev-parse", "HEAD"], folder).stdout == before_head
    # The run's pins are released.
    assert digest not in store.pinned()


def test_the_case_from_a_replay_starts_at_its_pinned_checkpoint_which_survives_the_cleanup(client, fake):
    project, folder, conversation_id, trace = fixture_run(client, fake)
    fake.responses.append(ScriptedResponse(text="Replayed."))
    started = start_replay(client, conversation_id, step_at(trace, 3)["id"])
    replay = finished_replay(client, conversation_id, started["id"])["replay"]

    response = client.post(f"/api/v1/conversations/{conversation_id}/replays/{replay['id']}/eval-case", json={})

    assert response.status_code == 201, response.text
    saved = response.json()
    assert saved["checkpoint_id"] == replay["checkpoint_id"] is not None
    case = parse_case(saved["name"], (folder / saved["path"]).read_text(encoding="utf-8")).case
    assert case is not None and case.start.checkpoint == replay["checkpoint_id"] and case.prompt == "Write hello"
    assert run_git(["log", "-1", "--format=%s"], folder).stdout.startswith(f"Add eval case {saved['name']}")
    store = SnapshotStore(trace_dir(client.app.state.data_dir))
    with client.app.state.session_factory() as session:
        assert session.get_one(Replay, replay["id"]).eval_case == saved["name"]
        pins = select(CheckpointPin.owner).where(CheckpointPin.checkpoint_id == case.start.checkpoint)
        assert list(session.scalars(pins)) == [case_owner(project["id"], saved["name"])]
        assert session.get_one(Replay, replay["id"]).snapshot in store.pinned()
        # Every checkpoint older than the 30 days: the cleanup removes all but the pinned one.
        session.execute(update(Checkpoint).values(created_at=datetime.now(UTC) - timedelta(days=40)))
        session.commit()
        store_ = CheckpointStore(client.app.state.session_factory, client.app.state.data_dir)
        store_.prune(session, store_.repository(project["id"], folder), project["id"])
        left = list(session.scalars(select(Checkpoint.id)))
        assert left == [case.start.checkpoint]
        # The case's workspace is made from it: the files of step 3.
        workspace = client.app.state.workspace_dir / "default" / ".evals" / "check"
        make_workspace(
            folder,
            workspace,
            case.start,
            client.app.state.data_dir,
            project["id"],
            lambda number: checkpoint_tree(session, project["id"], number),
            1,
        )
    assert (workspace / "hello.txt").read_text(encoding="utf-8") == "hello" and not (workspace / "other.txt").exists()
    again = client.post(f"/api/v1/conversations/{conversation_id}/replays/{replay['id']}/eval-case", json={})
    assert again.status_code == 409


def test_only_a_model_call_of_this_conversation_can_be_replayed(client, fake):
    _project, _folder, conversation_id, trace = fixture_run(client, fake)
    tool_step = step_at(trace, 2)

    refused = client.post(f"/api/v1/conversations/{conversation_id}/steps/{tool_step['id']}/replays", json={})
    unknown = client.post(f"/api/v1/conversations/{conversation_id}/steps/99999/replays", json={})
    bad_preset = client.post(
        f"/api/v1/conversations/{conversation_id}/steps/{step_at(trace, 1)['id']}/replays", json={"preset": "Nope"}
    )

    assert refused.status_code == 409 and "Only a model call" in refused.json()["detail"]
    assert unknown.status_code == 404
    assert bad_preset.status_code == 409 and "Nope" in bad_preset.json()["detail"]
    assert client.get(f"/api/v1/conversations/{conversation_id}/replays").json() == []


def test_removing_the_original_conversation_removes_its_replays(client, fake):
    _project, _folder, conversation_id, trace = fixture_run(client, fake)
    fake.responses.append(ScriptedResponse(text="Replayed."))
    replay = start_replay(client, conversation_id, step_at(trace, 3)["id"])
    replay = finished_replay(client, conversation_id, replay["id"])["replay"]

    assert client.delete(f"/api/v1/conversations/{conversation_id}").status_code == 204

    with client.app.state.session_factory() as session:
        assert session.get(Replay, replay["id"]) is None
        assert session.get(Conversation, replay["conversation_id"]) is None


@pytest.mark.parametrize(
    ("messages", "expected"),
    [
        (
            [HistoryMessage("user", [{"type": "image", "media_type": "image/png", "data": LEFT_OUT}])],
            [HistoryMessage("user", [{"type": "text", "text": LEFT_OUT_NOTE}])],
        ),
        (
            [HistoryMessage("assistant", [{"type": "reasoning", "text": "hm"}, {"type": "text", "text": "Hi."}])],
            [HistoryMessage("assistant", [{"type": "text", "text": "Hi."}])],
        ),
        (
            [
                HistoryMessage(
                    "assistant",
                    [
                        {"type": "text", "text": "Reading."},
                        {"type": "tool_call", "id": "a", "name": "read_file", "input": {"path": "x"}, "output": "1"},
                        {"type": "text", "text": "It says 1."},
                    ],
                )
            ],
            [
                HistoryMessage(
                    "assistant",
                    [
                        {"type": "text", "text": "Reading."},
                        {"type": "text", "text": '[Called the tool read_file (call a) with {"path": "x"}]'},
                    ],
                ),
                HistoryMessage("user", [{"type": "text", "text": "[The tool read_file (call a) returned:\n1]"}]),
                HistoryMessage("assistant", [{"type": "text", "text": "It says 1."}]),
            ],
        ),
        (
            [
                HistoryMessage(
                    "assistant",
                    [{"type": "tool_call", "id": "b", "name": "run_command", "output": "no", "is_error": True}],
                )
            ],
            [
                HistoryMessage(
                    "assistant", [{"type": "text", "text": "[Called the tool run_command (call b) with {}]"}]
                ),
                HistoryMessage("user", [{"type": "text", "text": "[The tool run_command (call b) failed:\nno]"}]),
            ],
        ),
    ],
)
def test_snapshot_messages_are_written_so_any_provider_reads_them(messages, expected):
    assert model_history(messages) == expected


def test_a_case_saved_from_a_conversation_pins_its_checkpoint_start(client, fake):
    project, _folder, _conversation_id, _trace = fixture_run(client, fake)
    with client.app.state.session_factory() as session:
        checkpoint_id = session.scalars(select(Checkpoint.id).order_by(Checkpoint.id)).first()
    case = {"name": "from-chat", "prompt": "Write hello", "checks": [{"kind": "rubric", "rubric": "It is written."}]}

    path = f"/api/v1/projects/{project['id']}/eval-cases"
    unknown = client.post(path, json={**case, "start": {"checkpoint": 999}})
    saved = client.post(path, json={**case, "start": {"checkpoint": checkpoint_id}})

    assert unknown.status_code == 422 and "no checkpoint 999" in unknown.json()["detail"]
    assert saved.status_code == 201, saved.text
    with client.app.state.session_factory() as session:
        pins = select(CheckpointPin.owner).where(CheckpointPin.checkpoint_id == checkpoint_id)
        assert list(session.scalars(pins)) == [case_owner(project["id"], "from-chat")]

def running_replay(client, fake) -> tuple[int, dict, Path]:
    """A replay whose reply streams slowly, once its workspace is there."""
    _project, _folder, conversation_id, trace = fixture_run(client, fake)
    fake.responses.append(ScriptedResponse(text=" ".join(["word"] * 200), delay=0.05))
    started = start_replay(client, conversation_id, step_at(trace, 3)["id"])
    with client.app.state.session_factory() as session:
        environment_id = session.get_one(Replay, started["id"]).environment_id
        slug = session.get_one(Environment, environment_id).slug
    workspace = workspace_folder(client.app.state.workspace_dir, slug, started["id"])
    for _ in range(100):
        detail = client.get(f"/api/v1/conversations/{conversation_id}/replays/{started['id']}").json()
        if detail["replay"]["conversation_id"] is not None and workspace.exists():
            break
        time.sleep(0.05)
    assert workspace.exists()
    return conversation_id, started, workspace


@pytest.mark.parametrize("how", ["stop", "shutdown"])
def test_a_replay_stopped_or_shut_down_while_playing_cleans_up_its_workspace(client, fake, how):
    conversation_id, started, workspace = running_replay(client, fake)
    state = client.app.state

    if how == "stop":
        response = client.post(f"/api/v1/conversations/{conversation_id}/replays/{started['id']}/stop")
        assert response.status_code == 204
    else:
        client.portal.call(stop_replies_and_their_work, state.replies, (state.evals.work, state.replay_service.work))

    # The stop waited for the replay's cleanup, which ran although the replay was being cancelled.
    with state.session_factory() as session:
        replay = session.get_one(Replay, started["id"])
        assert (replay.status, replay.detail) == ("stopped", "Stopped.")
        assert state.replies.running(replay.conversation_id) is None
    assert not workspace.exists()
    assert not state.replay_service.tasks
