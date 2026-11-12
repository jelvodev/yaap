"""The route of an answer: the stops a reply passed, built from what was kept for it (or recorded so far).

`build_route` is a pure function of `RouteFacts`, so the rules are table-testable without a database. A reply made
before notes were kept simply has fewer stops; nothing here fails on missing data or on a note kind it does not know.
"""

from dataclasses import dataclass
from typing import Literal

from app.core.why import why_from, why_out
from app.tracing.notes import NOTE_ITEM_CHARS, clipped
from app.tracing.route_facts import RouteFacts, RouteStep, live_step
from app.tracing.route_parts import (
    ToolStack,
    afterwards_stops,
    generic_note_stop,
    note_check,
    tokens_of,
    waiting_stop,
    worker_stop,
)
from app.tracing.route_schema import RouteCheckKind, RouteStopOut

Where = Literal["context", "tool", "afterwards"]


@dataclass(frozen=True)
class Place:
    """Where a note kind shows on the route: a check on the context stop, a check on the tool stop it followed
    (the context stop when no tool ran yet), or a stop after the answer."""

    where: Where
    check: RouteCheckKind = "note"


# A kind missing here is drawn as a generic stop of its own.
NOTE_PLACES: dict[str, Place] = {
    "memory": Place("context", "memory"),
    "cache": Place("context", "cache"),
    "guard": Place("tool", "guard"),
    "secrets_masked": Place("tool", "secrets"),
    "outgoing_blocked": Place("tool", "outgoing"),
    "summary": Place("afterwards"),
    "trim": Place("afterwards"),
}
# Why an answer did not come out whole, by the status of the reply's run.
ANSWER_FAILURES = {
    "error": "The reply ended with an error.",
    "refused": "The model refused to answer.",
    "incomplete": "The reply stopped before it was complete.",
}


def route_stop(route: dict[str, object] | None) -> RouteStopOut:
    if not route:
        return RouteStopOut(
            id="route", stage="route", lane="main", label="Route", state="skipped", reason="Routing off"
        )
    name = route.get("model_name") or route.get("model")
    kind = route.get("kind")
    choice = " · ".join(str(part) for part in (name, kind) if isinstance(part, str) and part)
    reason = route.get("reason")
    why = why_from(route.get("why"))
    return RouteStopOut(
        id="route",
        stage="route",
        lane="main",
        label="Route",
        detail=choice or None,
        why=why_out(why) if why else None,
        items=[clipped(reason, NOTE_ITEM_CHARS)] if isinstance(reason, str) and reason else [],
    )


def answer_stop(facts: RouteFacts) -> RouteStopOut | None:
    if facts.reply_status == "running":
        return None
    reason = ANSWER_FAILURES.get(facts.reply_status)
    return RouteStopOut(
        id="answer",
        stage="answer",
        lane="main",
        label="Answer",
        state="failed" if reason else "ran",
        reason=reason,
        run_id=facts.main_run_id,
    )


class MainLine:
    """The main run's steps in the order they happened: models in a row, tool calls stacked under the model before."""

    def __init__(self, run_id: int) -> None:
        self.run_id = run_id
        self.context = RouteStopOut(id="context", stage="context", lane="main", label="Context")
        self.entries: list[RouteStopOut | ToolStack] = []
        self.models = 0
        self.parent = "context"
        self.stack: ToolStack | None = None
        self.last_tool: ToolStack | None = None
        self.afterwards: list[RouteStep] = []

    def add(self, step: RouteStep) -> None:
        if step.kind == "model":
            self.add_model(step)
        elif step.kind == "tool":
            self.add_tool(step)
        else:
            self.add_note(step)

    def add_model(self, step: RouteStep) -> None:
        self.models += 1
        self.stack = None
        stop = RouteStopOut(
            id=f"model:{step.run_id}:{step.position}",
            stage="model",
            lane="main",
            label=f"Model {self.models}",
            detail=step.model,
            started_at=step.started_at,
            duration_ms=step.duration_ms,
            cost=step.cost,
            tokens=tokens_of(step.usage),
            run_id=step.run_id,
            step_id=step.step_id,
        )
        self.entries.append(stop)
        self.parent = stop.id

    def add_tool(self, step: RouteStep) -> None:
        tool = step.tool or "tool"
        if self.stack is not None and self.stack.tool == tool:
            self.stack.steps.append(step)
            return
        self.stack = self.last_tool = ToolStack(tool, self.parent, [step])
        self.entries.append(self.stack)

    def add_note(self, step: RouteStep) -> None:
        if step.note is None:
            return
        place = NOTE_PLACES.get(step.note.kind)
        if place is None:
            self.stack = None
            self.entries.append(generic_note_stop(step, "main", self.parent, None))
        elif place.where == "afterwards":
            self.afterwards.append(step)
        else:
            check = note_check(step.note, place.check)
            target = self.last_tool.checks if place.where == "tool" and self.last_tool else self.context.checks
            target.append(check)
            if place.where == "context":
                self.context.items.extend(step.note.items)

    def stops(self) -> list[RouteStopOut]:
        return [entry.stop() if isinstance(entry, ToolStack) else entry for entry in self.entries]


def run_lane_stops(facts: RouteFacts, main_stops: list[RouteStopOut]) -> list[RouteStopOut]:
    """One stop per helper run, hanging from the last main model call made before the helper's first step."""
    models = [stop for stop in main_stops if stop.stage == "model"]
    stops: list[RouteStopOut] = []
    for worker in facts.workers:
        if worker.run_id == facts.main_run_id:
            continue
        steps = sorted((s for s in facts.steps if s.run_id == worker.run_id), key=lambda s: s.position)
        begun = worker.started_at or (steps[0].started_at if steps else None)
        before = [m for m in models if m.started_at and begun and m.started_at <= begun]
        parent = before[-1].id if before else "context"
        checks = [note_check(s.note, NOTE_PLACES[s.note.kind].check) for s in steps if is_check_note(s)]
        waiting = any(call.run_id == worker.run_id for call in facts.waiting)
        stop = worker_stop(worker, [s for s in steps if s.kind != "note"], parent, checks, waiting)
        stops.append(stop)
        for step in steps:
            if step.note is not None and step.note.kind not in NOTE_PLACES:
                stops.append(generic_note_stop(step, "worker", stop.id, stop.line))
    return stops


def is_check_note(step: RouteStep) -> bool:
    place = NOTE_PLACES.get(step.note.kind) if step.note else None
    return place is not None and place.where != "afterwards"


def waiting_stops(facts: RouteFacts, line: MainLine) -> list[RouteStopOut]:
    stops: list[RouteStopOut] = []
    for call in facts.waiting:
        if call.run_id != facts.main_run_id:
            continue
        known = max((s.position for s in facts.steps if s.run_id == call.run_id), default=0)
        stops.append(waiting_stop(call, line.parent, call.position if call.position is not None else known + 1))
    return stops


def build_route(facts: RouteFacts) -> list[RouteStopOut]:
    """The stops of a reply in order: message, route, context, the main line, helpers, the answer, then the work
    done afterwards."""
    line = MainLine(facts.main_run_id)
    for step in sorted((s for s in facts.steps if s.run_id == facts.main_run_id), key=lambda s: s.position):
        line.add(step)
    main_stops = line.stops()
    stops = [
        RouteStopOut(id="message", stage="message", lane="main", label="Message"),
        route_stop(facts.route),
        line.context,
        *main_stops,
        *waiting_stops(facts, line),
        *run_lane_stops(facts, main_stops),
    ]
    answer = answer_stop(facts)
    if answer is not None:
        stops.append(answer)
    stops.extend(afterwards_stops(facts.afterwards, line.afterwards))
    return stops


__all__ = ["NOTE_PLACES", "Place", "build_route", "live_step"]
