"""The route of a running reply, sent as stops while the reply goes on.

The feed hears every step the recorder writes and rebuilds the route with the same builder the API uses, so the
drawing and the trace cannot disagree. It sends stops, not changes: a stop that is new or different from the one sent
last under its id is put on the queue, and the client replaces the stop with that id.
"""

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.tracing.recorder import ModelStep, NoteStep, ToolStep
from app.tracing.route import build_route, live_step
from app.tracing.route_facts import RouteFacts, RouteStep, WaitingCall, WorkerRun
from app.tracing.route_schema import RouteStopOut

# A run other than the reply's own is a helper; the live recorder does not know what it was asked to be.
HELPER_LABEL = "Helper"


@dataclass(frozen=True)
class RemovedStop:
    """A stop sent earlier that the route no longer has (a call that stopped waiting, a call stacked under another)."""

    id: str


def removed_ids(stops: list[RouteStopOut], sent: dict[str, str]) -> list[str]:
    """The ids sent earlier that are not in the route now; they are dropped from `sent`."""
    present = {stop.id for stop in stops}
    gone = [stop_id for stop_id in sent if stop_id not in present]
    for stop_id in gone:
        del sent[stop_id]
    return gone


def changed_stops(stops: list[RouteStopOut], sent: dict[str, str]) -> list[RouteStopOut]:
    """The stops that differ from what was sent last under their id; `sent` is updated to hold what is now sent."""
    changed: list[RouteStopOut] = []
    for stop in stops:
        text = json.dumps(stop.model_dump(mode="json"), sort_keys=True)
        if sent.get(stop.id) != text:
            sent[stop.id] = text
            changed.append(stop)
    return changed


class StageFeed:
    """Collects the steps of one reply and queues each route stop that is new or changed."""

    def __init__(self, route: dict[str, Any] | None) -> None:
        self.route = route
        self.main_run_id: int | None = None
        # After open() the queue is touched on the event loop only; before it, only `pending` is.
        # Items made before the stream starts (notes are recorded on a worker thread) wait in a list; the queue,
        # which belongs to the event loop, is made when the stream begins.
        self.pending: list[RouteStopOut | RemovedStop] = []
        self.queue: asyncio.Queue[RouteStopOut | RemovedStop] | None = None
        self.steps: list[RouteStep] = []
        self.waiting_calls: list[WaitingCall] = []
        self.sent: dict[str, str] = {}

    def open(self) -> "asyncio.Queue[RouteStopOut | RemovedStop]":
        """Called on the event loop when the stream starts: from here on items go straight onto the queue."""
        if self.queue is None:
            self.queue = asyncio.Queue()
            for item in self.pending:
                self.queue.put_nowait(item)
            self.pending = []
        return self.queue

    def emit(self, item: RouteStopOut | RemovedStop) -> None:
        if self.queue is None:
            self.pending.append(item)
        else:
            self.queue.put_nowait(item)

    def set_main_run(self, run_id: int) -> None:
        """The reply's own run is known: the first stops (message, route, context) go out, then those of any step
        heard before."""
        self.main_run_id = run_id
        self.publish()

    def on_step(self, run_id: int, step: ModelStep | ToolStep | NoteStep) -> None:
        self.steps.append(live_step(run_id, step))
        self.publish()

    def waiting(self, tool: str, summary: str | None) -> None:
        """A tool call of the reply waits for the user's decision."""
        if self.main_run_id is None:
            return
        self.waiting_calls = [WaitingCall(self.main_run_id, tool, summary, datetime.now(UTC))]
        self.publish()

    def answered(self) -> None:
        self.waiting_calls = []
        self.publish()

    def workers(self) -> list[WorkerRun]:
        first: dict[int, datetime] = {}
        for step in self.steps:
            if step.run_id != self.main_run_id:
                first.setdefault(step.run_id, step.started_at)
        return [WorkerRun(run_id, HELPER_LABEL, started_at=started) for run_id, started in first.items()]

    def publish(self) -> None:
        if self.main_run_id is None:
            return
        facts = RouteFacts(
            route=self.route,
            main_run_id=self.main_run_id,
            workers=self.workers(),
            steps=self.steps,
            waiting=self.waiting_calls,
            reply_status="running",
        )
        stops = build_route(facts)
        for stop_id in removed_ids(stops, self.sent):
            self.emit(RemovedStop(stop_id))
        for stop in changed_stops(stops, self.sent):
            self.emit(stop)
