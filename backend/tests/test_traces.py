"""The timeline: a reply's trace, what each model call saw, and the runs list a page at a time."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.providers.base import TokenUsage
from app.providers.fake_provider import ScriptedResponse
from app.storage.models import AgentRunRecord, RunStep
from app.tracing.snapshots import SnapshotStore, trace_dir
from tests.test_delegation import project_conversation
from tests.test_tracing import send, two_calls_three_tools

START = datetime(2026, 11, 8, 9, 0, tzinfo=UTC)


def reply_id(client, conversation_id: int) -> int:
    messages = client.get(f"/api/v1/conversations/{conversation_id}").json()["messages"]
    return next(message["id"] for message in reversed(messages) if message["role"] == "assistant")


def delegated_reply(client, fake) -> tuple[int, int]:
    """A reply that delegated one task; returns its conversation and its message."""
    _, conversation_id = project_conversation(client)
    fake.responses.extend(
        [
            ScriptedResponse(text="The worker made it.", tool_calls=(("delegate", {"task": "Create hello.txt"}),)),
            ScriptedResponse(
                text="Created.",
                tool_calls=(("write_file", {"path": "hello.txt", "content": "hi\n"}),),
                usage=TokenUsage(input_tokens=300, output_tokens=40),
            ),
        ]
    )
    send(client, conversation_id, "Make hello")
    return conversation_id, reply_id(client, conversation_id)


def add_run(session, conversation_id: int, parent: int | None, minutes: int, agent: str | None = None) -> int:
    record = AgentRunRecord(
        environment_id=1,
        conversation_id=conversation_id,
        model="m",
        agent=agent,
        status="complete",
        parent_run_id=parent,
        task=None if parent is None else f"task at {minutes}",
        started_at=START + timedelta(minutes=minutes),
        finished_at=START + timedelta(minutes=minutes + 1),
    )
    session.add(record)
    session.flush()
    return record.id


def add_step(session, run_id: int, position: int, minutes: int, tool: str | None = None) -> None:
    session.add(
        RunStep(
            run_id=run_id,
            position=position,
            kind="tool" if tool else "model",
            started_at=START + timedelta(minutes=minutes),
            duration_ms=500,
            model=None if tool else "m",
            tool=tool,
            cost=None if tool else 0.25,
        )
    )


def test_a_replys_trace_has_its_run_tree_and_the_steps_of_each_run(client, fake):
    conversation_id, message_id = delegated_reply(client, fake)

    response = client.get(f"/api/v1/conversations/{conversation_id}/messages/{message_id}/trace")

    assert response.status_code == 200
    trace = response.json()
    run = trace["run"]
    assert trace["conversation_id"] == conversation_id
    assert run["message_id"] == message_id and run["parent_run_id"] is None
    assert [(step["kind"], step["tool"]) for step in run["steps"]] == [("model", None), ("tool", "delegate")]
    (child,) = run["children"]
    assert child["parent_run_id"] == run["id"] and child["task"] == "Create hello.txt"
    assert [(step["kind"], step["tool"]) for step in child["steps"]] == [("model", None), ("tool", "write_file")]
    assert child["steps"][0]["input_tokens"] == 300 and child["children"] == []
    assert run["duration_ms"] is not None
    assert all(step["has_context"] for step in run["steps"] if step["kind"] == "model")

    by_run = client.get(f"/api/v1/conversations/{conversation_id}/runs/{child['id']}/trace").json()
    assert by_run["run"]["id"] == child["id"] and by_run["run"]["steps"] == child["steps"]


def test_a_trace_holds_three_levels_of_runs_with_costs_added_up(client):
    _, conversation_id = project_conversation(client)
    with client.app.state.session_factory() as session:
        root = add_run(session, conversation_id, None, 0)
        child = add_run(session, conversation_id, root, 1)
        grandchild = add_run(session, conversation_id, child, 2)
        add_step(session, root, 1, 0)
        add_step(session, root, 2, 1, tool="delegate")
        add_step(session, child, 1, 1)
        add_step(session, grandchild, 1, 2)
        session.commit()

    run = client.get(f"/api/v1/conversations/{conversation_id}/runs/{root}/trace").json()["run"]

    assert run["id"] == root and run["children"][0]["id"] == child
    assert run["children"][0]["children"][0]["id"] == grandchild
    assert run["cost"] == pytest.approx(0.25) and run["total_cost"] == pytest.approx(0.75)
    assert run["children"][0]["total_cost"] == pytest.approx(0.5)
    assert run["steps"][1]["cost"] is None and run["duration_ms"] == 60_000


def test_a_model_call_shows_what_it_saw_grouped_by_source_with_content_on_demand(client, fake, tmp_path):
    _, conversation_id = project_conversation(client)
    fake.responses.append(two_calls_three_tools())
    send(client, conversation_id, "Write and check a.txt")
    run = client.get(f"/api/v1/conversations/{conversation_id}/messages/{reply_id(client, conversation_id)}/trace")
    last = run.json()["run"]["steps"][-1]

    context = client.get(f"/api/v1/conversations/{conversation_id}/steps/{last['id']}/context").json()

    assert context["available"] and context["complete"]
    sources = [group["source"] for group in context["groups"]]
    assert sources[0] == "base" and "project" in sources and sources[-2:] == ["tools", "messages"]
    messages = context["groups"][-1]["parts"]
    assert messages[0]["label"].startswith("user: Write and check a.txt")
    assert "called write_file, read_file, list_files" in messages[-1]["label"]
    assert context["chars"] == sum(group["chars"] for group in context["groups"]) > 0
    assert all("content" not in part for group in context["groups"] for part in group["parts"])

    base = context["groups"][0]["parts"][0]
    content = client.get(f"/api/v1/conversations/{conversation_id}/steps/{last['id']}/context/{base['key']}").json()
    assert len(content["content"]) == base["chars"]
    missing = client.get(f"/api/v1/conversations/{conversation_id}/steps/{last['id']}/context/system-99")
    assert missing.status_code == 404

    # Once the snapshot is pruned, the step says so instead of failing.
    store = SnapshotStore(trace_dir(tmp_path))
    store.prune(now=datetime.now(UTC).timestamp() + 40 * 86400)
    gone = client.get(f"/api/v1/conversations/{conversation_id}/steps/{last['id']}/context").json()
    assert gone["available"] is False and gone["groups"] == []


def test_a_trace_is_refused_outside_its_conversation(client, fake):
    conversation_id, message_id = delegated_reply(client, fake)
    other = client.post(
        "/api/v1/environments/1/conversations", json={"model": "claude-opus-5-5", "effort": "medium"}
    ).json()["id"]
    with client.app.state.session_factory() as session:
        run = session.scalars(select(AgentRunRecord).where(AgentRunRecord.parent_run_id.is_(None))).one()
        step = session.scalars(select(RunStep).where(RunStep.run_id == run.id, RunStep.kind == "model")).first()
        assert step is not None
        run_id, step_id = run.id, step.id

    refused = [
        f"/api/v1/conversations/{other}/messages/{message_id}/trace",
        f"/api/v1/conversations/{other}/runs/{run_id}/trace",
        f"/api/v1/conversations/{other}/steps/{step_id}/context",
        f"/api/v1/conversations/{other}/steps/{step_id}/context/system-0",
        f"/api/v1/conversations/999/messages/{message_id}/trace",
        f"/api/v1/conversations/999/runs/{run_id}/trace",
        f"/api/v1/conversations/999/steps/{step_id}/context",
    ]
    assert [client.get(path).status_code for path in refused] == [404] * len(refused)
    assert client.get(f"/api/v1/conversations/{conversation_id}/runs/{run_id}/trace").status_code == 200

    client.cookies.clear()
    signed_out = client.get(f"/api/v1/conversations/{conversation_id}/runs/{run_id}/trace")
    assert signed_out.status_code == 401


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ({}, 9),
        ({"sub_runs": "false"}, 4),
        ({"agent": "reviewer"}, 5),
        ({"agent": "reviewer", "sub_runs": "false"}, 3),
    ],
)
def test_paging_returns_every_run_once_across_pages(client, query, expected):
    _, conversation_id = project_conversation(client)
    with client.app.state.session_factory() as session:
        for minutes in range(4):
            root = add_run(session, conversation_id, None, minutes * 10, agent="reviewer" if minutes < 2 else None)
            add_run(session, conversation_id, root, minutes * 10 + 1, agent="reviewer" if minutes == 3 else None)
        add_run(session, conversation_id, root, 39)
        session.commit()
    whole = client.get("/api/v1/environments/1/runs", params={**query, "limit": 200}).json()

    paged: list[int] = []
    before: int | None = None
    while True:
        params = {**query, "limit": 2, **({"before": before} if before else {})}
        page = client.get("/api/v1/environments/1/runs", params=params).json()
        paged += [run["id"] for run in page]
        if len(page) < 2:
            break
        before = page[-1]["id"]

    assert len(whole) == expected
    assert paged == [run["id"] for run in whole] and len(set(paged)) == len(paged)
    assert paged == sorted(paged, reverse=True)
