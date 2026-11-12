"""The live route: which stops a running reply sends, and the stage events in its stream."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import anyio
import pytest

from app.core.chat_events import server_sent_event
from app.core.reply_events import with_stage_events
from app.tracing.notes import Note
from app.tracing.recorder import ModelStep, NoteStep, ToolStep
from app.tracing.route_feed import RemovedStop, StageFeed, changed_stops, removed_ids
from app.tracing.route_schema import RouteStopOut
from app.providers.base import TokenUsage

NOW = datetime(2026, 11, 8, 9, 0, tzinfo=UTC)
MAIN = 1


def model(position: int) -> ModelStep:
    return ModelStep(position, NOW, 10, "m", TokenUsage(1, 1), 0.1)


def tool(position: int, name: str = "read_file") -> ToolStep:
    return ToolStep(position, NOW, 5, name, f"{name} {position}")


def note(position: int, kind: str = "memory") -> NoteStep:
    return NoteStep(position, NOW, Note(kind=kind, text="A note.", items=["one"]))  # type: ignore[arg-type]  # kind is a plain string in the table


def drain(feed: StageFeed) -> list[RouteStopOut]:
    queue = feed.open()
    stops: list[RouteStopOut] = []
    while not queue.empty():
        item = queue.get_nowait()
        if isinstance(item, RouteStopOut):
            stops.append(item)
    return stops


def removed(feed: StageFeed) -> list[str]:
    queue = feed.open()
    ids: list[str] = []
    while not queue.empty():
        item = queue.get_nowait()
        if isinstance(item, RemovedStop):
            ids.append(item.id)
    return ids


def started() -> StageFeed:
    feed = StageFeed(None)
    feed.set_main_run(MAIN)
    drain(feed)
    return feed


def test_the_first_stops_go_out_when_the_main_run_is_known():
    feed = StageFeed(None)
    feed.on_step(MAIN, model(1))
    assert drain(feed) == []
    feed.set_main_run(MAIN)
    assert [stop.id for stop in drain(feed)] == ["message", "route", "context", f"model:{MAIN}:1"]


def test_a_stacked_tool_is_sent_again_with_the_same_id_and_a_higher_count():
    feed = started()
    feed.on_step(MAIN, model(1))
    feed.on_step(MAIN, tool(2))
    first = [stop for stop in drain(feed) if stop.stage == "tool"]
    feed.on_step(MAIN, tool(3))
    again = drain(feed)
    assert [(stop.id, stop.count) for stop in first] == [(f"tool:{MAIN}:2", 1)]
    assert [(stop.id, stop.count) for stop in again] == [(f"tool:{MAIN}:2", 2)]


def test_a_note_on_the_context_sends_the_context_stop_again():
    feed = started()
    feed.on_step(MAIN, note(1))
    stops = drain(feed)
    assert [stop.id for stop in stops] == ["context"]
    assert [check.kind for check in stops[0].checks] == ["memory"]


def test_a_run_other_than_the_main_one_becomes_a_helper():
    feed = started()
    feed.on_step(MAIN, model(1))
    feed.on_step(7, tool(1))
    workers = [stop for stop in drain(feed) if stop.stage == "worker"]
    assert [(stop.id, stop.label, stop.line) for stop in workers] == [("worker:7", "Helper", "run-7")]


def test_nothing_is_sent_again_when_the_route_did_not_change():
    feed = started()
    feed.on_step(MAIN, model(1))
    drain(feed)
    feed.publish()
    assert drain(feed) == []


def test_a_call_waiting_for_the_user_shows_as_a_waiting_stop_until_it_is_answered():
    feed = started()
    feed.on_step(MAIN, model(1))
    drain(feed)
    feed.waiting("run_command", "ls")
    waiting = drain(feed)
    assert [(stop.stage, stop.state) for stop in waiting] == [("tool", "waiting")]
    feed.answered()
    assert removed(feed) == [waiting[0].id]


def test_nothing_is_removed_when_the_route_did_not_change():
    feed = started()
    feed.on_step(MAIN, model(1))
    drain(feed)
    feed.publish()
    assert removed(feed) == []


@pytest.mark.parametrize(
    ("sent", "present", "gone"),
    [
        ({"a": "x", "b": "y"}, ["a", "b"], []),
        ({"a": "x", "b": "y"}, ["a"], ["b"]),
        ({}, ["a"], []),
    ],
)
def test_ids_sent_but_no_longer_built_are_removed(sent, present, gone):
    stops = [RouteStopOut(id=i, stage="route", lane="main", label=i) for i in present]
    assert removed_ids(stops, sent) == gone
    assert set(sent) <= set(present)


def test_steps_heard_before_the_stream_starts_wait_for_it():
    feed = StageFeed(None)
    feed.set_main_run(MAIN)
    feed.on_step(MAIN, note(1))
    assert feed.queue is None
    assert "context" in [stop.id for stop in drain(feed)]


@pytest.mark.parametrize(
    ("sent_before", "expected"),
    [
        ({}, ["a", "b"]),
        ({"a": "same", "b": "other"}, ["b"]),
    ],
)
def test_only_new_or_different_stops_are_chosen(sent_before, expected, monkeypatch):
    stops = [RouteStopOut(id="a", stage="route", lane="main", label="A"), RouteStopOut(id="b", stage="route", lane="main", label="B")]
    sent = dict(sent_before)
    if "a" in sent:
        sent["a"] = json.dumps(stops[0].model_dump(mode="json"), sort_keys=True)
    assert [stop.id for stop in changed_stops(stops, sent)] == expected
    assert changed_stops(stops, sent) == []


def test_stage_events_are_interleaved_with_the_replys_events():
    anyio.run(check_interleaving)


async def check_interleaving() -> None:
    feed = started()

    async def events() -> AsyncIterator[str]:
        yield server_sent_event("text_delta", {})
        feed.on_step(MAIN, model(1))
        await asyncio.sleep(0.05)
        yield server_sent_event("done", {})

    received = [item async for item in with_stage_events(feed, events())]
    names = [item.splitlines()[0] for item in received]
    assert names[0] == "event: text_delta"
    assert names.count("event: stage") == 1
    assert names[-1] == "event: done"
    stage = next(item for item in received if item.startswith("event: stage"))
    assert json.loads(stage.split("data: ", 1)[1])["id"] == f"model:{MAIN}:1"


def test_stops_queued_when_the_reply_ends_go_out_before_the_end_event():
    anyio.run(check_order_at_end)


async def check_order_at_end() -> None:
    feed = started()

    async def events() -> AsyncIterator[str]:
        feed.on_step(MAIN, model(1))
        yield server_sent_event("done", {})

    names = [item.splitlines()[0] async for item in with_stage_events(feed, events())]
    assert names.index("event: done") == len(names) - 1
    assert "event: stage" in names


def test_an_approval_request_in_the_stream_puts_a_waiting_stop_on_the_route():
    anyio.run(check_waiting)


async def check_waiting() -> None:
    feed = started()
    feed.on_step(MAIN, model(1))
    drain(feed)

    async def events() -> AsyncIterator[str]:
        yield server_sent_event("approval_request", {"summary": "ls", "tool": "run_command"})
        await asyncio.sleep(0.05)
        yield server_sent_event("approval_resolved", {"decision": "allow"})

    received = [item async for item in with_stage_events(feed, events())]
    states = [json.loads(i.split("data: ", 1)[1])["state"] for i in received if i.startswith("event: stage" + chr(10))]
    assert states == ["waiting"]
    assert any(i.startswith("event: stage_removed") for i in received)
