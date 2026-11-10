"""Moving a conversation to another project: what moves, what stays, and when it is refused."""

import pytest
from sqlalchemy import select

from app.storage.models import (
    AgentRunRecord,
    Checkpoint,
    Conversation,
    Goal,
    LearningProposal,
    MemorySuggestion,
    Message,
    QueuedMessage,
    Review,
    SearchChunk,
)
from tests.test_chat import start_conversation

MODEL = {"model": "claude-opus-5-5", "effort": "medium"}


def make_project(client, name: str, environment_id: int = 1) -> int:
    return client.post(f"/api/v1/environments/{environment_id}/projects", json={"name": name}).json()["id"]


def project_conversation(client, project_id: int | None) -> int:
    body = {**MODEL, **({"project_id": project_id} if project_id is not None else {})}
    return client.post("/api/v1/environments/1/conversations", json=body).json()["id"]


def seed(client, conversation_id: int, project_id: int) -> dict[str, int]:
    """One row of everything that points at the conversation; the ids of those rows by name."""
    with client.app.state.session_factory() as session:
        message = Message(conversation_id=conversation_id, role="user", content=[{"type": "text", "text": "Hi"}])
        session.add(message)
        session.flush()
        session.get(Conversation, conversation_id).head_message_id = message.id
        rows = {
            "runs": AgentRunRecord(
                environment_id=1, conversation_id=conversation_id, project_id=project_id, model="m", status="complete"
            ),
            "chunks": SearchChunk(
                kind="message",
                source_key=f"message:{message.id}",
                chunk_no=0,
                environment_id=1,
                project_id=project_id,
                conversation_id=conversation_id,
                message_id=message.id,
                text="Hi",
                content_hash="x",
            ),
            "reviews": Review(
                environment_id=1,
                project_id=project_id,
                conversation_id=conversation_id,
                source="conversation",
                status="done",
            ),
            "suggestions": MemorySuggestion(
                project_id=project_id, conversation_id=conversation_id, section="Facts", text="t", status="open"
            ),
            "proposals": LearningProposal(
                project_id=project_id,
                conversation_id=conversation_id,
                kind="rule",
                level="project",
                target="a.md",
                source="utility",
                measurement="not_needed",
                text="t",
                status="open",
            ),
            "checkpoints": Checkpoint(
                project_id=project_id,
                conversation_id=conversation_id,
                kind="edit",
                tool="write",
                summary="s",
                commit="c",
                tree="t",
            ),
        }
        session.add_all(rows.values())
        session.commit()
        return {name: row.id for name, row in rows.items()}


def project_of(client, table: type, row_id: int) -> int | None:
    with client.app.state.session_factory() as session:
        return session.get(table, row_id).project_id


TABLES = {
    "runs": AgentRunRecord,
    "chunks": SearchChunk,
    "reviews": Review,
    "suggestions": MemorySuggestion,
    "proposals": LearningProposal,
    "checkpoints": Checkpoint,
}


@pytest.mark.parametrize(
    ("table", "to_project", "moves"),
    [
        ("runs", True, True),
        ("chunks", True, True),
        ("reviews", True, True),
        ("suggestions", True, True),
        ("proposals", True, True),
        ("checkpoints", True, False),
        ("runs", False, True),
        ("chunks", False, True),
        # These need a project, so they stay with the old one when the conversation leaves projects.
        ("reviews", False, False),
        ("suggestions", False, False),
        ("proposals", False, False),
        ("checkpoints", False, False),
    ],
)
def test_move_changes_the_project_of_what_belongs_to_the_conversation(client, table, to_project, moves):
    old, new = make_project(client, "Shop"), make_project(client, "Site")
    conversation_id = project_conversation(client, old)
    rows = seed(client, conversation_id, old)

    response = client.post(
        f"/api/v1/conversations/{conversation_id}/move", json={"project_id": new if to_project else None}
    )

    assert response.status_code == 200
    assert response.json()["project_id"] == (new if to_project else None)
    expected = (new if to_project else None) if moves else old
    assert project_of(client, TABLES[table], rows[table]) == expected


def test_move_adds_a_note_the_model_sees(client):
    old, new = make_project(client, "Shop"), make_project(client, "Site")
    conversation_id = project_conversation(client, old)
    seed(client, conversation_id, old)

    client.post(f"/api/v1/conversations/{conversation_id}/move", json={"project_id": new})

    messages = client.get(f"/api/v1/conversations/{conversation_id}").json()["messages"]
    assert [(block["type"], block["text"]) for block in messages[-1]["content"]] == [
        ("note", "Moved from Shop to Site.")
    ]
    client.post(f"/api/v1/conversations/{conversation_id}/move", json={"project_id": None})
    messages = client.get(f"/api/v1/conversations/{conversation_id}").json()["messages"]
    assert messages[-1]["content"][0]["text"] == "Moved from Site to No project."


@pytest.mark.parametrize("busy", ["goal", "queued"])
def test_move_is_refused_while_the_conversation_works_or_waits(client, busy):
    old, new = make_project(client, "Shop"), make_project(client, "Site")
    conversation_id = project_conversation(client, old)
    with client.app.state.session_factory() as session:
        if busy == "goal":
            session.add(
                Goal(conversation_id=conversation_id, objective="o", status="active", max_turns=3, max_cost=0, turns=0)
            )
        else:
            session.add(QueuedMessage(conversation_id=conversation_id, text="later", position=0))
        session.commit()

    response = client.post(f"/api/v1/conversations/{conversation_id}/move", json={"project_id": new})

    assert response.status_code == 409
    assert project_of(client, Conversation, conversation_id) == old


def test_move_is_refused_while_a_reply_runs(client):
    old, new = make_project(client, "Shop"), make_project(client, "Site")
    conversation_id = project_conversation(client, old)
    client.app.state.replies.jobs[conversation_id] = object()

    response = client.post(f"/api/v1/conversations/{conversation_id}/move", json={"project_id": new})

    client.app.state.replies.jobs.clear()
    assert response.status_code == 409


def test_move_is_refused_when_a_reply_starts_while_the_work_is_stopped(client, monkeypatch):
    old, new = make_project(client, "Shop"), make_project(client, "Site")
    conversation_id = project_conversation(client, old)

    async def stop_and_reply(*args, **kwargs):
        client.app.state.replies.jobs[conversation_id] = object()
        return 0, 0

    monkeypatch.setattr("app.core.conversation_move.stop_work", stop_and_reply)

    response = client.post(f"/api/v1/conversations/{conversation_id}/move", json={"project_id": new})

    client.app.state.replies.jobs.clear()
    assert response.status_code == 409
    assert project_of(client, Conversation, conversation_id) == old


def test_move_to_a_project_of_another_environment_is_refused(client):
    other = client.post("/api/v1/environments", json={"name": "Other", "colour": "#123456"}).json()["id"]
    foreign = make_project(client, "Foreign", other)
    conversation_id = start_conversation(client)

    response = client.post(f"/api/v1/conversations/{conversation_id}/move", json={"project_id": foreign})

    assert response.status_code == 422
    assert project_of(client, Conversation, conversation_id) is None


@pytest.mark.parametrize(("project_id", "status"), [(9999, 404), ("same", 422)])
def test_move_rejects_unknown_and_unchanged_targets(client, project_id, status):
    old = make_project(client, "Shop")
    conversation_id = project_conversation(client, old)

    response = client.post(
        f"/api/v1/conversations/{conversation_id}/move", json={"project_id": old if project_id == "same" else project_id}
    )

    assert response.status_code == status


def test_moved_conversation_is_listed_under_its_new_project(client):
    old, new = make_project(client, "Shop"), make_project(client, "Site")
    conversation_id = project_conversation(client, old)

    client.post(f"/api/v1/conversations/{conversation_id}/move", json={"project_id": new})

    listed = client.get("/api/v1/environments/1/conversations").json()
    assert [item["project_id"] for item in listed if item["id"] == conversation_id] == [new]
    with client.app.state.session_factory() as session:
        assert session.scalars(select(Conversation.project_id).where(Conversation.id == conversation_id)).one() == new
