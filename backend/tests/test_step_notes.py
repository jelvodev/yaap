"""Step notes: stored beside the model and tool steps, read back apart from them, and out of every step count."""

import anyio
import pytest

from app.core.why import Why
from app.providers.base import TokenUsage
from app.tracing.notes import (
    NOTE_MAX_ITEMS,
    Note,
    append_note,
    call_whys,
    note_from,
    run_notes,
)
from app.tracing.recorder import ModelStep, NoteStep, ToolStep, TraceRecorder
from app.tracing.steps import find_step, run_steps, steps_of_runs
from tests.test_delegation import project_conversation
from tests.test_traces import START, add_run

WHY = Why("Old output is trimmed past the limit.", "trim", "trim_at", "global")


def make_run(client) -> int:
    _, conversation_id = project_conversation(client)
    with client.app.state.session_factory() as session:
        run_id = add_run(session, conversation_id, None, 0)
        session.commit()
    return run_id


def tool_step(position: int, **extra) -> ToolStep:
    return ToolStep(position, START, 5, "run_command", "ls", **extra)


def test_a_note_written_through_the_recorder_reads_back_with_its_reason(client):
    run_id = make_run(client)
    recorder = TraceRecorder(client.app.state.session_factory)
    note = Note("trim", "Trimmed 3 old outputs.", WHY, cost=0.5, items=("a", "b"), count=3)

    anyio.run(recorder.note, run_id, note)

    with client.app.state.session_factory() as session:
        (view,) = run_notes(session, [run_id])
    assert view.note == note and view.run_id == run_id and view.position == 1


def test_notes_are_not_steps_of_a_run(client):
    run_id = make_run(client)
    recorder = TraceRecorder(client.app.state.session_factory)

    async def record() -> None:
        await recorder.record(run_id, ModelStep(recorder.reserve(run_id), START, 10, "m", TokenUsage(1, 1)))
        await recorder.note(run_id, Note("cache", "Cached for an hour."))
        await recorder.record(run_id, tool_step(recorder.reserve(run_id)))

    anyio.run(record)

    with client.app.state.session_factory() as session:
        steps = run_steps(session, run_id)
        by_run = steps_of_runs(session, [run_id])
        (view,) = run_notes(session, [run_id])
        assert find_step(session, view.step_id) is None
    assert [step.kind for step in steps] == ["model", "tool"]
    assert [step.kind for step in by_run[run_id]] == ["model", "tool"]


def test_the_listener_is_told_about_notes_as_well_as_model_and_tool_steps(client):
    run_id = make_run(client)
    told: list[object] = []
    recorder = TraceRecorder(client.app.state.session_factory, listener=lambda _run, step: told.append(step))
    tool = tool_step(1)

    async def record() -> None:
        await recorder.note(run_id, Note("memory", "Recalled 2 entries."))
        await recorder.record(run_id, tool)

    anyio.run(record)

    assert [type(step).__name__ for step in told] == ["NoteStep", "ToolStep"]


def test_a_note_made_from_a_worker_thread_takes_the_next_position(client):
    run_id = make_run(client)
    recorder = TraceRecorder(client.app.state.session_factory)

    recorder.note_now(run_id, Note("guard", "Checked."))
    recorder.note_now(run_id, Note("guard", "Checked again."))

    with client.app.state.session_factory() as session:
        assert [view.position for view in run_notes(session, [run_id])] == [1, 2]


def test_append_note_goes_after_the_last_step(client):
    run_id = make_run(client)
    recorder = TraceRecorder(client.app.state.session_factory)
    anyio.run(recorder.record, run_id, tool_step(4))

    with client.app.state.session_factory() as session:
        append_note(session, run_id, Note("trim", "Trimmed after the reply."))
        (view,) = run_notes(session, [run_id])
    assert view.position == 5


def test_call_whys_returns_the_reason_and_call_key_of_a_tool_step(client):
    run_id = make_run(client)
    recorder = TraceRecorder(client.app.state.session_factory)
    why = Why("Commands ask before they run.", "permission", "access", "project")

    async def record() -> None:
        await recorder.record(run_id, tool_step(1))
        await recorder.record(run_id, tool_step(2, approval="asked", why=why, call_key="abc"))
        await recorder.note(run_id, Note("trim", "Trimmed.", WHY))

    anyio.run(record)

    with client.app.state.session_factory() as session:
        (found,) = call_whys(session, [run_id])
    assert (found.tool, found.call_key, found.approval, found.why) == ("run_command", "abc", "asked", why)


@pytest.mark.parametrize(
    "detail",
    [
        None,
        "text",
        {},
        {"note": "unknown", "text": "x"},
        {"note": "trim"},
        {"note": "trim", "text": 5},
    ],
)
def test_note_from_rejects_values_that_are_not_notes(detail):
    assert note_from(detail) is None


def test_note_from_ignores_malformed_optional_parts():
    note = note_from({"note": "memory", "text": "x", "why": "junk", "cost": "1", "items": [1, "a"], "count": True})

    assert note == Note("memory", "x", None, None, ("a",), None)


def test_a_notes_text_and_items_are_capped():
    note = Note("memory", "t" * 400, items=tuple("i" * 300 for _ in range(30)))

    detail = note.detail()

    assert len(detail["text"]) == 300
    assert len(detail["items"]) == NOTE_MAX_ITEMS and all(len(item) == 200 for item in detail["items"])


def test_a_note_step_is_a_step_row_of_kind_note():
    from app.tracing.recorder import step_row

    row = step_row(1, NoteStep(3, START, Note("summary", "Summarised.")))

    assert (row.kind, row.position, row.duration_ms, row.summary) == ("note", 3, 0, "Summarised.")
