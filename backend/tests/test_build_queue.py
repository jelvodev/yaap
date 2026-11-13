"""The build queue's decision: who may build now, and why a plan waits."""

from datetime import UTC, datetime, timedelta

import pytest

from app.plans.model import Plan, PlanStep
from app.plans.queue import (
    BuildSlot,
    QueueLimits,
    Start,
    Wait,
    can_start,
    files_overlap,
    normalised,
    parallel_limit,
    plan_files,
    rate_limited,
)

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


def limits(parallel: int = 2, held_for: timedelta | None = None) -> QueueLimits:
    return QueueLimits(parallel, NOW + held_for if held_for is not None else None, NOW)


def slot(plan_id: int, files: tuple[str, ...] | None, card_id: int | None = None) -> BuildSlot:
    return BuildSlot(plan_id, card_id, None if files is None else frozenset(normalised(path) for path in files))


@pytest.mark.parametrize(
    ("running", "parallel", "expected"),
    [
        ([], 1, "start"),
        ([slot(1, ("a.py",))], 1, "limit"),
        ([slot(1, ("a.py",))], 2, "start"),
        ([slot(1, ("a.py",)), slot(2, ("b.py",))], 2, "limit"),
        ([slot(1, ("a.py",)), slot(2, ("b.py",))], 3, "start"),
    ],
)
def test_a_build_waits_when_the_projects_slots_are_all_in_use(
    running: list[BuildSlot], parallel: int, expected: str
) -> None:
    outcome = can_start(slot(9, ("c.py",)), running, {}, limits(parallel))
    assert (outcome.kind if isinstance(outcome, Wait) else "start") == expected


@pytest.mark.parametrize(
    ("mine", "other", "overlaps"),
    [
        (("a.py",), ("b.py",), False),
        (("a.py",), ("a.py",), True),
        (("src/a.py",), ("src",), True),
        (("src",), ("src/a.py",), True),
        (("src/a.py",), ("src/b.py",), False),
        (("src/a.py",), ("srcs/a.py",), False),
        (("./src/a.py",), ("src/a.py",), True),
        (("src/*.py",), ("src/b.py",), True),
        (("src/*.py",), ("docs/b.md",), False),
        (("*.py",), ("docs/b.md",), True),
        (("a.py", "docs/x.md"), ("docs/",), True),
        (None, ("a.py",), True),
        (("a.py",), None, True),
        (None, None, True),
    ],
)
def test_builds_whose_plans_share_files_do_not_run_together(
    mine: tuple[str, ...] | None, other: tuple[str, ...] | None, overlaps: bool
) -> None:
    outcome = can_start(slot(9, mine), [slot(1, other)], {}, limits(4))
    if overlaps:
        assert isinstance(outcome, Wait)
        assert outcome.kind == "files"
        assert outcome.blocking == (1,)
    else:
        assert isinstance(outcome, Start)


def test_a_plan_without_files_waits_for_every_other_build_and_says_why() -> None:
    outcome = can_start(slot(9, None), [slot(1, ("a.py",), card_id=5)], {}, limits(4))
    assert isinstance(outcome, Wait)
    assert "whole project" in outcome.reason
    assert "#5" in outcome.reason


def test_a_plan_without_files_starts_when_nothing_else_builds() -> None:
    assert isinstance(can_start(slot(9, None), [], {}, limits(4)), Start)


@pytest.mark.parametrize(
    ("waits", "expected_blocking"),
    [({}, None), ({7: ()}, None), ({7: (3,)}, (3,)), ({7: (3, 4)}, (3, 4)), ({8: (3,)}, None)],
)
def test_a_card_waits_for_the_cards_it_waits_for(
    waits: dict[int, tuple[int, ...]], expected_blocking: tuple[int, ...] | None
) -> None:
    outcome = can_start(slot(9, ("a.py",), card_id=7), [], waits, limits())
    if expected_blocking is None:
        assert isinstance(outcome, Start)
    else:
        assert isinstance(outcome, Wait)
        assert outcome.kind == "waits_for"
        assert outcome.blocking == expected_blocking
        assert all(f"#{card}" in outcome.reason for card in expected_blocking)


def test_a_plan_without_a_card_never_waits_for_cards() -> None:
    # A mapping that has an entry under None must not be consulted for a plan without a card.
    waits: dict[int, tuple[int, ...]] = {0: (3,)}
    assert isinstance(can_start(slot(9, ("a.py",)), [], waits, limits()), Start)


@pytest.mark.parametrize(
    ("held_for", "starts"),
    [
        (None, True),
        (timedelta(minutes=5), False),
        (timedelta(seconds=1), False),
        (timedelta(0), True),
        (timedelta(minutes=-1), True),
    ],
)
def test_a_provider_rate_limit_holds_new_builds_until_the_hold_ends(held_for: timedelta | None, starts: bool) -> None:
    outcome = can_start(slot(9, ("a.py",)), [], {}, limits(held_for=held_for))
    assert isinstance(outcome, Start) is starts
    if isinstance(outcome, Wait):
        assert outcome.kind == "rate_limit"


def test_the_reason_to_wait_is_decided_by_waits_first_then_rate_limit_then_slots_then_files() -> None:
    busy = [slot(1, ("a.py",))]
    both = can_start(slot(9, ("a.py",), 7), busy, {7: (3,)}, limits(1, timedelta(minutes=5)))
    assert isinstance(both, Wait) and both.kind == "waits_for"
    held = can_start(slot(9, ("a.py",), 7), busy, {}, limits(1, timedelta(minutes=5)))
    assert isinstance(held, Wait) and held.kind == "rate_limit"
    full = can_start(slot(9, ("a.py",), 7), busy, {}, limits(1))
    assert isinstance(full, Wait) and full.kind == "limit"
    overlap = can_start(slot(9, ("a.py",), 7), busy, {}, limits(2))
    assert isinstance(overlap, Wait) and overlap.kind == "files"


@pytest.mark.parametrize(
    ("report", "expected"),
    [
        ("Rate limit reached for this model", True),
        ("HTTP 429 Too Many Requests", True),
        ("You have hit your usage limit", True),
        ("Quota exceeded", True),
        ("The tests failed", False),
        ("", False),
    ],
)
def test_a_failed_attempt_is_a_rate_limit_only_when_the_provider_says_so(report: str, expected: bool) -> None:
    assert rate_limited(report) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(1, 1), ("3", 3), (" 4 ", 4), (4, 4), (0, 2), (5, 2), (-1, 2), ("many", 2), ("", 2), (None, 2)],
)
def test_the_parallel_builds_setting_is_a_count_from_one_to_four(value: object, expected: int) -> None:
    assert parallel_limit(value) == expected


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/a.py", "src/a.py"),
        ("./src/a.py", "src/a.py"),
        ("src/", "src"),
        ("src/*.py", "src"),
        ("src/**/x.py", "src"),
        ("*.py", ""),
        ("src/a[0-9].py", "src"),
        ("  src/a.py  ", "src/a.py"),
    ],
)
def test_paths_are_compared_as_directory_prefixes(path: str, expected: str) -> None:
    assert normalised(path) == expected


def test_plan_files_collects_every_step_and_is_none_when_a_step_names_no_files() -> None:
    named = Plan("t", steps=(PlanStep(1, "a", files=("a.py",)), PlanStep(2, "b", files=("./b/", "c.py"))))
    assert plan_files(named) == frozenset({"a.py", "b", "c.py"})
    loose = Plan("t", steps=(PlanStep(1, "a", files=("a.py",)), PlanStep(2, "b")))
    assert plan_files(loose) is None


def test_files_overlap_treats_an_empty_path_as_the_whole_project() -> None:
    assert files_overlap(frozenset({""}), frozenset({"a.py"}))
    assert not files_overlap(frozenset(), frozenset({"a.py"}))
