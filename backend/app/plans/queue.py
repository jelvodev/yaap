"""Who may build now: the rules of the build queue as pure functions. No I/O and no clock (the caller passes `now`).

A plan starts when its card waits for nothing open, no provider rate limit holds new builds, the project has a free
build slot and no running build names files that overlap its own. Otherwise it waits, with a reason to show.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from app.plans.model import Plan

RATE_LIMIT_HOLD = timedelta(minutes=10)
DEFAULT_PARALLEL = 2
MAX_PARALLEL = 4
GLOB_CHARACTERS = "*?["
# The sentences YAAP's own providers give for a limit they hit (lower case).
RATE_LIMIT_MARKERS = ("rate limit", "usage limit", "http 429", "quota")

WaitKind = Literal["waits_for", "rate_limit", "limit", "files"]


@dataclass(frozen=True)
class BuildSlot:
    """A plan that builds, or wants to."""

    plan_id: int
    card_id: int | None
    # None: some step names no files, so the plan counts as the whole project.
    files: frozenset[str] | None


@dataclass(frozen=True)
class QueueLimits:
    parallel: int
    held_until: datetime | None
    now: datetime


@dataclass(frozen=True)
class Start:
    """The plan may build now."""


@dataclass(frozen=True)
class Wait:
    kind: WaitKind
    # Shown on the card after "Queued: ".
    reason: str
    # Plan ids (files) or card ids (waits_for) it waits for.
    blocking: tuple[int, ...] = ()


def normalised(path: str) -> str:
    """The path as a comparable prefix: no leading "./" or trailing "/", a glob cut to the directory before it
    ("" is the whole project)."""
    text = path.strip()
    while text.startswith("./"):
        text = text[2:]
    text = text.rstrip("/")
    cut = next((index for index, character in enumerate(text) if character in GLOB_CHARACTERS), None)
    if cut is not None:
        prefix = text[:cut]
        text = prefix.rsplit("/", 1)[0] if "/" in prefix else ""
    return text


def plan_files(plan: Plan) -> frozenset[str] | None:
    """Every file the plan's steps name, or None when a step names none (it may touch anything)."""
    files: set[str] = set()
    for step in plan.steps:
        if not step.files:
            return None
        files.update(normalised(path) for path in step.files)
    return frozenset(files)


def paths_overlap(a: str, b: str) -> bool:
    """Whether two normalised paths are the same or one is a directory above the other."""
    if not a or not b or a == b:
        return True
    return a.startswith(b + "/") or b.startswith(a + "/")


def files_overlap(a: frozenset[str] | None, b: frozenset[str] | None) -> bool:
    if a is None or b is None:
        return True
    return any(paths_overlap(left, right) for left in a for right in b)


def parallel_limit(value: object) -> int:
    """The `board_parallel_builds` setting as a count from 1 to 4."""
    try:
        count = int(str(value).strip())
    except ValueError:
        return DEFAULT_PARALLEL
    return count if 1 <= count <= MAX_PARALLEL else DEFAULT_PARALLEL


def rate_limited(report: str) -> bool:
    """Whether a failed attempt's report is a provider's rate or usage limit."""
    text = report.lower()
    return any(marker in text for marker in RATE_LIMIT_MARKERS)


def slot_name(slot: BuildSlot) -> str:
    return f"#{slot.card_id}" if slot.card_id is not None else f"plan {slot.plan_id}"


def can_start(
    plan: BuildSlot, running: Sequence[BuildSlot], cards: Mapping[int, tuple[int, ...]], limits: QueueLimits
) -> Start | Wait:
    """Whether `plan` may build now. `running` holds the other builds of the project; `cards` maps a card to the
    cards it still waits for."""
    waits = cards.get(plan.card_id, ()) if plan.card_id is not None else ()
    if waits:
        names = ", ".join(f"#{card}" for card in waits)
        return Wait("waits_for", f"Waits for {names}.", waits)
    if limits.held_until is not None and limits.now < limits.held_until:
        until = limits.held_until.strftime("%H:%M")
        return Wait("rate_limit", f"A provider's rate limit was reached; new builds wait until {until} UTC.")
    if len(running) >= limits.parallel:
        return Wait("limit", f"All {limits.parallel} build slots of the project are in use.")
    for other in running:
        if not files_overlap(plan.files, other.files):
            continue
        name = slot_name(other)
        if other.files is None:
            reason = f"Waits for {name}: one of its steps names no files, so it may touch the whole project."
            return Wait("files", reason, (other.plan_id,))
        if plan.files is None:
            reason = f"Waits for {name}: a step of this plan names no files, so it needs the whole project."
            return Wait("files", reason, (other.plan_id,))
        return Wait("files", f"Waits for {name}'s files.", (other.plan_id,))
    return Start()
