"""The shapes of the route of an answer: the stops it passed, the checks on each and the tokens they used.

The API returns them whole (`RouteOut`) and a running reply streams them one stop at a time (`event: stage`, the data
is one `RouteStopOut`); a stop with the same id replaces the earlier one.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.core.why import WhyOut

RouteStage = Literal["message", "route", "context", "model", "tool", "worker", "answer", "afterwards", "note"]
RouteLane = Literal["main", "tools", "worker", "afterwards"]
RouteState = Literal["ran", "skipped", "failed", "waiting"]
RouteCheckKind = Literal[
    "permission", "approval", "guard", "outgoing", "secrets", "output_stored", "memory", "cache", "trim", "note"
]


class RouteTokensOut(BaseModel):
    new: int
    cache_read: int
    cache_write: int
    output: int


class RouteCheckOut(BaseModel):
    kind: RouteCheckKind
    state: RouteState
    text: str
    why: WhyOut | None = None
    waited_ms: int | None = None


class RouteStopOut(BaseModel):
    # Stable across the live event and the API: "message", "route", "context", "answer", "model:<run>:<position>",
    # "tool:<run>:<position of the first call in the stack>", "worker:<run>", "note:<run>:<position>", "after:<task>".
    id: str
    stage: RouteStage
    lane: RouteLane
    # The worker lane only: "run-<run id>".
    line: str | None = None
    # The main-line stop a tool branch, a worker line or an afterwards stop leaves from.
    parent: str | None = None
    label: str
    # Calls stacked into this stop (read_file x12), 1 otherwise.
    count: int = 1
    state: RouteState = "ran"
    # Why the stop was skipped or failed.
    reason: str | None = None
    # What happened, in one line.
    detail: str | None = None
    started_at: datetime | None = None
    duration_ms: int | None = None
    cost: float | None = None
    tokens: RouteTokensOut | None = None
    why: WhyOut | None = None
    checks: list[RouteCheckOut] = []
    # The stacked calls' summaries, a worker's tools ("read_file x3"), memory entries.
    items: list[str] = []
    run_id: int | None = None
    # The first step's row id, to open it in the timeline; None while the reply streams.
    step_id: int | None = None


class RouteOut(BaseModel):
    stops: list[RouteStopOut]
    duration_ms: int | None = None
    cost: float | None = None
