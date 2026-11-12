"""The route of an answer: the pure builder from facts to stops, and the API that serves it."""

from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from app.core.why import Why
from app.providers.base import TokenUsage
from app.storage.models import AgentRunRecord, Message, RunStep, UsageRecord
from app.tracing.notes import ApprovalOutcome, Note, NoteKind
from app.tracing.route import build_route
from app.tracing.route_facts import RouteFacts, RouteStep, UtilityUse, WaitingCall, WorkerRun
from app.tracing.route_schema import RouteStopOut
from tests.test_delegation import project_conversation

START = datetime(2026, 11, 8, 9, 0, tzinfo=UTC)
WHY = Why("Because.", "permission", "access", "project")
ROUTE = {"model_name": "Sonnet", "kind": "question", "reason": "A short question.", "why": WHY.stored()}


def model(position: int, run_id: int = 1, cost: float | None = 0.5) -> RouteStep:
    usage = TokenUsage(10, 2, 30, 5)
    at = START + timedelta(seconds=position)
    return RouteStep(run_id, position, "model", at, 100, model="m", usage=usage, cost=cost)


def tool(
    position: int,
    name: str,
    run_id: int = 1,
    summary: str | None = None,
    error: bool = False,
    approval: ApprovalOutcome | None = None,
    why: Why | None = None,
    output_ref: int | None = None,
) -> RouteStep:
    return RouteStep(
        run_id, position, "tool", START + timedelta(seconds=position), 10, tool=name,
        summary=summary or f"{name} {position}",
        is_error=error, approval=approval, why=why, output_ref=output_ref, step_id=100 + position,
    )  # fmt: skip


def note(position: int, kind: str, run_id: int = 1, text: str = "A note.") -> RouteStep:
    made = Note(cast(NoteKind, kind), text, WHY, items=("a", "b"))
    return RouteStep(run_id, position, "note", START + timedelta(seconds=position), summary=text, note=made)


def facts(steps: list[RouteStep], **extra: object) -> RouteFacts:
    values: dict[str, object] = {"route": ROUTE, "main_run_id": 1, "steps": steps}
    return RouteFacts(**{**values, **extra})  # type: ignore[arg-type]  # a table of keyword overrides


def by_id(stops: list[RouteStopOut]) -> dict[str, RouteStopOut]:
    return {stop.id: stop for stop in stops}


def ids(stops: list[RouteStopOut]) -> list[str]:
    return [stop.id for stop in stops]


def test_three_reads_then_an_edit_stack_into_one_read_stop_and_one_edit_stop_under_their_model():
    reads_then_edit = [tool(2, "read_file"), tool(3, "read_file"), tool(4, "read_file"), tool(5, "edit_file")]
    stops = build_route(facts([model(1), *reads_then_edit]))

    assert ids(stops) == ["message", "route", "context", "model:1:1", "tool:1:2", "tool:1:5", "answer"]
    reads, edit = by_id(stops)["tool:1:2"], by_id(stops)["tool:1:5"]
    assert (reads.count, reads.lane, reads.parent) == (3, "tools", "model:1:1")
    assert (reads.duration_ms, reads.step_id) == (30, 102)
    assert reads.items == ["read_file 2", "read_file 3", "read_file 4"] and reads.detail == "3 calls"
    assert (edit.count, edit.label, edit.detail) == (1, "edit_file", "edit_file 5")


def test_the_same_tool_after_another_model_call_starts_a_new_stack():
    stops = build_route(facts([model(1), tool(2, "read_file"), model(3), tool(4, "read_file")]))

    tools = [(s.id, s.parent) for s in stops if s.stage == "tool"]
    assert tools == [("tool:1:2", "model:1:1"), ("tool:1:4", "model:1:3")]


def test_a_worker_line_has_one_stop_with_summed_cost_counted_tools_and_a_parent_on_the_main_line():
    helper = [model(5, 2, 0.25), tool(6, "read_file", 2), tool(7, "read_file", 2), tool(8, "grep", 2)]
    steps = [model(1), model(10), *helper]
    begin, end = START + timedelta(seconds=4), START + timedelta(seconds=9)
    worker = WorkerRun(2, "Explorer", begin, end, "complete", "Look around")

    stops = by_id(build_route(facts(steps, workers=[worker])))

    stop = stops["worker:2"]
    assert (stop.stage, stop.lane, stop.line, stop.parent) == ("worker", "worker", "run-2", "model:1:1")
    assert (stop.label, stop.state) == ("Explorer", "ran")
    assert (stop.count, stop.cost, stop.duration_ms, stop.detail) == (4, 0.25, 5000, "Look around")
    assert stop.items == ["read_file ×2", "grep"] and stop.tokens is not None and stop.tokens.output == 2
    assert "model:2:5" not in stops


def test_a_failed_worker_run_is_a_failed_stop_with_the_error_as_reason():
    steps = [model(1), tool(5, "run_command", 2, "boom", error=True)]

    stop = by_id(build_route(facts(steps, workers=[WorkerRun(2, "")])))["worker:2"]

    assert (stop.state, stop.reason, stop.label) == ("failed", "boom", "Helper")


@pytest.mark.parametrize(
    ("route", "state", "reason", "detail"),
    [
        (None, "skipped", "Routing off", None),
        ({}, "skipped", "Routing off", None),
        (ROUTE, "ran", None, "Sonnet · question"),
    ],
)
def test_the_route_stop_is_skipped_without_a_stored_route(route, state, reason, detail):
    stop = by_id(build_route(facts([model(1)], route=route)))["route"]

    assert (stop.state, stop.reason, stop.detail) == (state, reason, detail)
    assert (stop.why is not None) == (state == "ran")


@pytest.mark.parametrize(
    ("approval", "state", "reason", "check_state"),
    [
        ("approved", "ran", None, "ran"),
        ("declined", "failed", "Declined", "failed"),
        ("refused", "failed", "Refused by the permission rules", "failed"),
        ("asked", "skipped", "Nobody to approve", "skipped"),
    ],
)
def test_an_approval_decides_the_state_of_the_tool_stop_and_adds_a_check(approval, state, reason, check_state):
    steps = [model(1), tool(2, "run_command", approval=approval, why=WHY, output_ref=3)]

    stop = by_id(build_route(facts(steps)))["tool:1:2"]

    assert (stop.state, stop.reason) == (state, reason)
    assert [(c.kind, c.state) for c in stop.checks] == [
        ("permission", check_state), ("approval", check_state), ("output_stored", "ran")
    ]  # fmt: skip
    assert stop.checks[0].why is not None and stop.checks[0].why.rule == "Because."


def test_a_call_that_waits_for_an_answer_is_a_waiting_tool_stop_with_a_waiting_approval_check():
    call = WaitingCall(1, "run_command", "npm test", START + timedelta(seconds=9))

    stops = by_id(build_route(facts([model(1), tool(2, "read_file")], waiting=[call], reply_status="running")))

    waiting = stops["tool:1:3"]
    assert (waiting.state, waiting.parent, waiting.detail) == ("waiting", "model:1:1", "npm test")
    assert [(c.kind, c.state) for c in waiting.checks] == [("approval", "waiting")]
    assert "answer" not in stops


def test_a_failed_tool_call_is_a_failed_stop_with_the_error_as_reason():
    stop = by_id(build_route(facts([model(1), tool(2, "run_command", summary="exit 1", error=True)])))["tool:1:2"]

    assert (stop.state, stop.reason) == ("failed", "exit 1")


def test_an_unknown_note_kind_is_drawn_as_a_generic_stop_on_its_own_lane():
    steps = [model(1), note(2, "escalated_model", text="Moved to a larger model."), note(5, "delegated", run_id=2)]

    stops = by_id(build_route(facts(steps, workers=[WorkerRun(2, "Explorer")])))

    generic = stops["note:1:2"]
    assert (generic.stage, generic.lane, generic.label, generic.detail, generic.state) == (
        "note", "main", "Escalated model", "Moved to a larger model.", "ran"
    )  # fmt: skip
    assert generic.items == ["a", "b"] and generic.why is not None
    worker_note = stops["note:2:5"]
    assert (worker_note.lane, worker_note.line, worker_note.label) == ("worker", "run-2", "Delegated")


@pytest.mark.parametrize(
    ("steps", "stop_id", "lane", "parent"),
    [
        ([model(1), tool(2, "edit_file"), note(3, "escalation")], "note:1:3", "main", "model:1:1"),
        ([model(1), note(2, "escalation", run_id=2)], "note:2:2", "worker", "worker:2"),
    ],
)
def test_an_escalation_note_is_a_stop_of_its_own_where_the_lead_took_the_work_back(steps, stop_id, lane, parent):
    stops = by_id(build_route(facts(steps, workers=[WorkerRun(2, "Worker")])))

    stop = stops[stop_id]
    assert (stop.stage, stop.lane, stop.parent, stop.label, stop.state) == ("note", lane, parent, "Escalation", "ran")
    assert stop.items == ["a", "b"] and stop.why is not None and stop.checks == []


@pytest.mark.parametrize(
    ("kind", "check_kind"),
    [("memory", "memory"), ("cache", "cache")],
)
def test_context_notes_become_checks_on_the_context_stop_and_memory_entries_become_its_items(kind, check_kind):
    stop = by_id(build_route(facts([note(1, kind), model(2)])))["context"]

    assert [(c.kind, c.state) for c in stop.checks] == [(check_kind, "ran")]
    assert stop.items == ["a", "b"]


@pytest.mark.parametrize(
    ("kind", "check_kind"), [("guard", "guard"), ("secrets_masked", "secrets"), ("outgoing_blocked", "outgoing")]
)
def test_call_notes_become_checks_on_the_preceding_tool_stop_or_on_the_context_before_any_tool(kind, check_kind):
    stops = by_id(build_route(facts([note(1, kind), model(2), tool(3, "read_file"), note(4, kind)])))

    assert [c.kind for c in stops["context"].checks] == [check_kind]
    assert [c.kind for c in stops["tool:1:3"].checks] == [check_kind]


def test_a_reply_without_notes_or_a_route_has_fewer_stops_and_no_error():
    stops = build_route(RouteFacts(None, 1, steps=[model(1, cost=None)]))

    assert ids(stops) == ["message", "route", "context", "model:1:1", "answer"]
    assert stops[3].cost is None and stops[2].checks == []


@pytest.mark.parametrize(
    ("status", "expected"),
    [("running", None), ("complete", "ran"), ("error", "failed"), ("refused", "failed"), ("incomplete", "failed")],
)
def test_the_answer_stop_follows_the_status_of_the_reply(status, expected):
    stops = by_id(build_route(facts([model(1)], reply_status=status)))

    assert (stops["answer"].state if "answer" in stops else None) == expected


def test_afterwards_stops_come_from_usage_records_and_merge_the_summary_note():
    uses = [
        UtilityUse("title_and_memory", "haiku", 0.01, START, "Titled it"),
        UtilityUse("summary", "haiku", 0.02, START),
        UtilityUse("route", "haiku", 0.5, START),
        UtilityUse("odd_task", None, None, START),
    ]

    steps = [model(1), note(2, "summary", text="Summarised."), note(3, "trim")]
    stops = by_id(build_route(facts(steps, afterwards=uses)))

    assert [s.id for s in stops.values() if s.stage == "afterwards"] == [
        "after:title_and_memory", "after:summary", "after:odd_task", "after:trim"
    ]  # fmt: skip
    title = stops["after:title_and_memory"]
    assert (title.label, title.detail) == ("Title and memory", "Titled it")
    summary = stops["after:summary"]
    assert (summary.lane, summary.parent, summary.cost, summary.detail, summary.why is not None) == (
        "afterwards", "answer", 0.02, "Summarised.", True
    )  # fmt: skip
    assert stops["after:odd_task"].label == "Odd task" and stops["after:trim"].label == "Shorter old output"


def test_a_stop_survives_a_round_trip_through_json():
    steps = [model(1), tool(2, "run_command", approval="approved", why=WHY), note(3, "guard")]

    for stop in build_route(facts(steps, afterwards=[UtilityUse("title", "m", 0.1, START)])):
        assert RouteStopOut.model_validate(stop.model_dump(mode="json")) == stop


def add_reply(client, conversation_id: int) -> tuple[int, int]:
    with client.app.state.session_factory() as session:
        message = Message(conversation_id=conversation_id, role="assistant", content=[], route=ROUTE)
        session.add(message)
        session.flush()
        run = AgentRunRecord(
            environment_id=1, conversation_id=conversation_id, message_id=message.id, model="m", status="complete",
            started_at=START, finished_at=START + timedelta(seconds=3),
        )  # fmt: skip
        session.add(run)
        session.flush()
        session.add(RunStep(run_id=run.id, position=1, kind="model", started_at=START, duration_ms=1, model="m",
                            input_tokens=10, output_tokens=2, cache_read_tokens=0, cache_write_tokens=0,
                            cost=0.5))  # fmt: skip
        session.add(UsageRecord(environment_id=1, conversation_id=conversation_id, message_id=message.id, provider="p",
                                model="m", purpose="utility", task="title", cost=0.25))  # fmt: skip
        session.commit()
        return message.id, run.id


def test_the_route_api_serves_the_stops_of_a_reply_and_refuses_another_conversations_message(client):
    _, conversation_id = project_conversation(client)
    _, other_id = project_conversation(client)
    message_id, run_id = add_reply(client, conversation_id)

    response = client.get(f"/api/v1/conversations/{conversation_id}/messages/{message_id}/route")

    assert response.status_code == 200
    body = response.json()
    expected = ["message", "route", "context", f"model:{run_id}:1", "answer", "after:title"]
    assert [stop["id"] for stop in body["stops"]] == expected
    assert (body["duration_ms"], body["cost"]) == (3000, 0.75)
    assert client.get(f"/api/v1/conversations/{other_id}/messages/{message_id}/route").status_code == 404
