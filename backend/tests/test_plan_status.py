"""The plan's status machine: every pair of status and event, in one table."""

import itertools
from typing import get_args

import pytest

from app.plans.model import Plan, PlanEvent, PlanStatus, PlanStep, next_status, plan_progress

STATUSES: tuple[PlanStatus, ...] = get_args(PlanStatus)
EVENTS: tuple[PlanEvent, ...] = get_args(PlanEvent)

ALLOWED: dict[tuple[PlanStatus | None, PlanEvent], PlanStatus] = {
    (None, "submit"): "draft",
    ("draft", "edit"): "draft",
    ("draft", "approve"): "approved",
    ("approved", "build_started"): "building",
    ("approved", "queue"): "queued",
    ("queued", "build_started"): "building",
    ("building", "all_steps_done"): "done",
    ("building", "step_failed"): "failed",
    ("building", "stop"): "failed",
    ("building", "restart_lost"): "interrupted",
    ("interrupted", "continue"): "building",
    ("failed", "continue"): "building",
    ("draft", "discard"): "cancelled",
    ("approved", "discard"): "cancelled",
    ("queued", "discard"): "cancelled",
    ("interrupted", "discard"): "cancelled",
    ("failed", "discard"): "cancelled",
    ("draft", "replan"): "cancelled",
    ("approved", "replan"): "cancelled",
    ("queued", "replan"): "cancelled",
    ("interrupted", "replan"): "cancelled",
    ("failed", "replan"): "cancelled",
}


@pytest.mark.parametrize(
    ("current", "event"), list(itertools.product((None, *STATUSES), EVENTS)), ids=lambda value: str(value)
)
def test_next_status_follows_the_table_for_every_pair(current, event):
    assert next_status(current, event) == ALLOWED.get((current, event))


@pytest.mark.parametrize("status", ["done", "cancelled"])
def test_a_finished_plan_accepts_no_event(status):
    assert all(next_status(status, event) is None for event in EVENTS)


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ((), (0, 0)),
        (("open", "open"), (0, 2)),
        (("done", "working", "open"), (1, 3)),
        (("done", "skipped", "failed"), (2, 3)),
    ],
)
def test_plan_progress_counts_done_and_skipped_steps(statuses, expected):
    plan = Plan("T", steps=tuple(PlanStep(index, "s", status=status) for index, status in enumerate(statuses, 1)))
    assert plan_progress(plan) == expected
