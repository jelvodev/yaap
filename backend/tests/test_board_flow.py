"""The card flow: where each event takes a card, what is refused and why, what is needed next, and the tile's state."""

import itertools

import pytest

from app.board.card import COLUMNS
from app.board.flow import (
    CardFacts,
    NextStep,
    Refusal,
    Transition,
    card_state,
    next_step,
    review_from_status,
    step,
)


def facts(column: str = "inbox", **overrides) -> CardFacts:
    return CardFacts(column=column, **overrides)  # type: ignore[arg-type]  # the tests pass plain strings


def outcome(result: Transition | Refusal) -> str:
    return result.column if isinstance(result, Transition) else f"refused:{result.code}"


def index(column: str) -> int:
    return COLUMNS.index(column)  # type: ignore[arg-type]


# --- Moves by hand ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("column", "target", "overrides", "expected"),
    [
        ("inbox", "idea", {}, "idea"),
        ("inbox", "design", {}, "design"),
        ("inbox", "plan", {}, "refused:one_column"),
        ("inbox", "build", {}, "refused:one_column"),
        ("inbox", "done", {}, "refused:one_column"),
        ("idea", "design", {}, "design"),
        ("idea", "plan", {}, "refused:one_column"),
        ("idea", "review", {}, "refused:one_column"),
        ("design", "plan", {"has_specification": True}, "plan"),
        ("design", "plan", {}, "plan"),
        ("design", "build", {}, "refused:one_column"),
        ("design", "done", {}, "refused:one_column"),
        ("plan", "build", {}, "refused:use_approve"),
        ("plan", "build", {"plan_status": "draft"}, "refused:use_approve"),
        ("plan", "review", {}, "refused:one_column"),
        ("plan", "done", {}, "refused:one_column"),
        ("build", "review", {"plan_status": "done"}, "review"),
        ("build", "review", {"plan_status": "failed"}, "refused:build_not_finished"),
        ("build", "review", {"plan_status": "interrupted"}, "refused:build_not_finished"),
        ("build", "review", {"plan_status": "building"}, "refused:build_not_finished"),
        ("build", "review", {}, "refused:build_not_finished"),
        ("build", "done", {"plan_status": "done"}, "refused:one_column"),
        ("review", "done", {"review": "done"}, "done"),
        ("review", "done", {"review": "running"}, "refused:no_review"),
        ("review", "done", {}, "refused:no_review"),
        # Backwards: any earlier column, but a plan must be made again unless it is still a draft.
        ("done", "review", {}, "review"),
        ("done", "inbox", {}, "inbox"),
        ("review", "build", {}, "build"),
        ("review", "plan", {"plan_status": "done"}, "refused:use_send_back"),
        ("review", "plan", {"plan_status": "draft"}, "plan"),
        ("review", "design", {"plan_status": "done"}, "design"),
        ("build", "design", {"plan_status": "failed"}, "design"),
        ("build", "plan", {"plan_status": "failed"}, "refused:use_send_back"),
        ("plan", "design", {"plan_status": "draft"}, "design"),
        ("plan", "idea", {}, "idea"),
        ("design", "inbox", {}, "inbox"),
        # A running or queued build must be stopped before the card moves back.
        ("build", "design", {"plan_status": "building"}, "refused:build_running"),
        ("build", "plan", {"plan_status": "queued"}, "refused:build_running"),
        ("review", "idea", {"plan_status": "building"}, "refused:build_running"),
        # Staying in the column is not an error.
        ("build", "build", {"plan_status": "building"}, "build"),
        ("idea", "idea", {}, "idea"),
    ],
)
def test_a_user_move_follows_the_table(column, target, overrides, expected) -> None:
    assert outcome(step(facts(column, **overrides), "move", "user", target)) == expected


def test_a_move_without_a_target_is_refused() -> None:
    assert outcome(step(facts("idea"), "move", "user")) == "refused:no_target"


def test_the_agent_may_only_move_a_card_into_the_inbox() -> None:
    for column, target in itertools.product(COLUMNS, COLUMNS):
        allowed = target == "inbox"
        for actor in ("agent", "trigger"):
            result = step(facts(column), "move", actor, target)
            if allowed:
                assert outcome(result) == "inbox", (column, actor)
            else:
                assert outcome(result) == "refused:not_allowed", (column, target, actor)


STAGE_EVENTS = ["accept", "drop", "specify_done", "plan_approved", "mark_done", "send_back", "merged"]


@pytest.mark.parametrize("event", STAGE_EVENTS)
@pytest.mark.parametrize("actor", ["agent", "trigger"])
def test_events_are_refused_for_the_agent_and_triggers(event, actor) -> None:
    assert outcome(step(facts("inbox"), event, actor)) == "refused:not_allowed"


@pytest.mark.parametrize("actor", ["user", "yaap"])
def test_a_forward_design_to_plan_move_needs_a_specification_only_for_non_users(actor) -> None:
    result = outcome(step(facts("design"), "move", actor, "plan"))
    assert result == ("plan" if actor == "user" else "refused:needs_specification")
    assert outcome(step(facts("design", has_specification=True), "move", actor, "plan")) == "plan"


@pytest.mark.parametrize("event", ["accept", "move", "drop", "plan_approved", "build_done", "send_back", "mark_done"])
@pytest.mark.parametrize("actor", ["user", "yaap"])
def test_a_broken_card_takes_no_event(event, actor) -> None:
    result = step(facts("inbox", broken=True), event, actor, "idea")
    assert isinstance(result, Refusal) and result.code == "card_broken"


def test_a_move_never_changes_more_than_the_table_allows_for_any_facts() -> None:
    """Sweep every pair of columns and combination of facts: forward moves are at most one column, except the
    inbox to design shortcut, and nothing ever lands on build by hand."""
    statuses = (None, "draft", "approved", "queued", "building", "done", "failed", "interrupted", "cancelled")
    for column, target, status, review in itertools.product(COLUMNS, COLUMNS, statuses, ("none", "running", "done")):
        result = step(facts(column, plan_status=status, review=review), "move", "user", target)
        if isinstance(result, Transition) and index(target) > index(column):
            assert index(target) - index(column) == 1 or (column, target) == ("inbox", "design")
            assert target != "build"
        if column == target:
            assert result == Transition(column)


# --- Events ----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("event", "column", "overrides", "target", "expected"),
    [
        ("accept", "inbox", {}, None, "idea"),
        ("accept", "inbox", {}, "design", "design"),
        ("accept", "inbox", {}, "plan", "refused:one_column"),
        ("accept", "idea", {}, None, "refused:wrong_column"),
        ("drop", "inbox", {}, None, "inbox"),
        ("drop", "review", {"plan_status": "done"}, None, "review"),
        ("drop", "build", {"plan_status": "building"}, None, "refused:build_running"),
        ("drop", "plan", {"plan_status": "queued"}, None, "refused:build_running"),
        ("specify_done", "idea", {"has_specification": True}, None, "design"),
        ("specify_done", "idea", {}, None, "idea"),
        ("specify_done", "design", {}, None, "design"),
        ("specify_done", "plan", {}, None, "refused:wrong_column"),
        ("plan_submitted", "design", {}, None, "plan"),
        ("plan_submitted", "plan", {}, None, "plan"),
        ("plan_submitted", "idea", {}, None, "refused:wrong_column"),
        ("plan_approved", "plan", {}, None, "build"),
        ("plan_approved", "plan", {"open_waits": (3, 4)}, None, "refused:waits_for"),
        ("plan_approved", "design", {}, None, "refused:wrong_column"),
        ("build_done", "build", {"plan_status": "done"}, None, "review"),
        ("build_done", "build", {"plan_status": "building"}, None, "refused:build_not_finished"),
        ("build_done", "plan", {"plan_status": "done"}, None, "refused:wrong_column"),
        ("build_failed", "build", {"plan_status": "failed"}, None, "build"),
        ("build_failed", "review", {}, None, "refused:wrong_column"),
        ("review_done", "review", {}, None, "review"),
        ("review_done", "build", {}, None, "refused:wrong_column"),
        ("send_back", "review", {}, None, "design"),
        ("send_back", "review", {"plan_status": "done"}, None, "design"),
        ("send_back", "build", {"plan_status": "failed"}, None, "design"),
        ("send_back", "build", {"plan_status": "interrupted"}, None, "design"),
        ("send_back", "build", {"plan_status": "cancelled"}, None, "design"),
        ("send_back", "build", {"plan_status": "done"}, None, "design"),
        ("send_back", "build", {"plan_status": "building"}, None, "refused:build_running"),
        ("send_back", "build", {"plan_status": "queued"}, None, "refused:build_running"),
        ("send_back", "build", {}, None, "refused:build_running"),
        ("send_back", "plan", {}, None, "refused:wrong_column"),
        ("send_back", "done", {}, None, "refused:wrong_column"),
        ("mark_done", "review", {}, None, "done"),
        ("mark_done", "build", {}, None, "refused:wrong_column"),
        # J: a merge into the version ends the card, a failed merge reopens its build.
        ("merged", "review", {"review": "done"}, None, "done"),
        ("merged", "review", {}, None, "refused:wrong_column"),
        ("merged", "done", {}, None, "done"),
        ("merged", "build", {"review": "done"}, None, "refused:wrong_column"),
        ("merge_failed", "review", {}, None, "build"),
        ("merge_failed", "done", {}, None, "build"),
        ("merge_failed", "build", {}, None, "refused:wrong_column"),
        ("merge_failed", "idea", {}, None, "refused:wrong_column"),
    ],
)
def test_an_event_moves_the_card_or_is_refused(event, column, overrides, target, expected) -> None:
    assert outcome(step(facts(column, **overrides), event, "user", target)) == expected


YAAP_EVENTS = ["merged", "merge_failed", "build_done", "build_failed", "review_done", "plan_submitted"]


@pytest.mark.parametrize("event", YAAP_EVENTS)
def test_yaap_causes_the_stage_events_and_they_move_at_most_one_column(event) -> None:
    for column in COLUMNS:
        result = step(facts(column, plan_status="done", review="done"), event, "yaap")
        if isinstance(result, Transition):
            assert abs(index(result.column) - index(column)) <= 2


def test_merge_failed_names_what_happened_in_the_log_note() -> None:
    assert step(facts("review"), "merge_failed", "yaap") == Transition("build", "merge into the version failed")
    assert step(facts("review", review="done"), "merged", "yaap") == Transition("done", "merged into the version")


def test_mark_done_by_a_non_user_needs_a_review_but_the_user_may_skip_it() -> None:
    assert outcome(step(facts("review"), "mark_done", "yaap")) == "refused:no_review"
    assert outcome(step(facts("review", review="done"), "mark_done", "yaap")) == "done"
    assert outcome(step(facts("review"), "mark_done", "user")) == "done"


def test_a_refusal_carries_a_sentence_for_the_user() -> None:
    result = step(facts("plan", open_waits=(3, 12)), "plan_approved", "user")
    assert isinstance(result, Refusal) and result.reason == "Waits for #3, #12."


# --- What is needed next ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "action", "who"),
    [
        ({"column": "inbox"}, "accept", "you"),
        ({"column": "idea"}, "specify", "you"),
        ({"column": "design"}, "plan", "you"),
        ({"column": "plan", "plan_status": "draft"}, "approve", "you"),
        ({"column": "plan", "plan_status": "draft", "open_waits": (2,)}, "none", "you"),
        ({"column": "plan", "plan_status": "queued"}, "build", "yaap"),
        ({"column": "plan", "plan_status": "approved"}, "build", "yaap"),
        ({"column": "plan", "plan_status": "building"}, "none", "yaap"),
        ({"column": "plan"}, "plan", "you"),
        ({"column": "build", "plan_status": "building"}, "none", "yaap"),
        ({"column": "build", "plan_status": "queued"}, "build", "yaap"),
        ({"column": "build", "plan_status": "approved"}, "build", "yaap"),
        ({"column": "build", "plan_status": "interrupted"}, "continue", "you"),
        ({"column": "build", "plan_status": "failed"}, "continue", "you"),
        ({"column": "build", "plan_status": "done"}, "none", "you"),
        ({"column": "build", "plan_status": "cancelled"}, "none", "you"),
        ({"column": "build"}, "plan", "you"),
        ({"column": "review"}, "review", "you"),
        ({"column": "review", "review": "running"}, "none", "yaap"),
        ({"column": "review", "review": "done"}, "judge", "you"),
        ({"column": "review", "review": "done", "evidence_ok": False}, "judge", "you"),
        ({"column": "done"}, "none", "you"),
        ({"column": "idea", "open_questions": 1}, "answer", "you"),
        ({"column": "build", "plan_status": "building", "open_questions": 2}, "answer", "you"),
        ({"column": "done", "broken": True}, "none", "you"),
        ({"column": "inbox", "broken": True, "open_questions": 1}, "none", "you"),
    ],
)
def test_next_step_names_the_action_and_who_does_it(overrides, action, who) -> None:
    found = next_step(facts(**overrides))
    assert isinstance(found, NextStep) and (found.action, found.who) == (action, who)
    assert found.label


def test_next_step_says_when_a_review_found_problems() -> None:
    plain = next_step(facts("review", review="done", evidence_ok=True)).label
    problems = next_step(facts("review", review="done", evidence_ok=False)).label
    assert plain == "Judge the review" and "problems" in problems


def test_next_step_lists_the_cards_a_plan_waits_for() -> None:
    assert next_step(facts("plan", plan_status="draft", open_waits=(2, 9))).label == "Waits for #2, #9."


# --- The tile's state ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "streaming", "approval", "expected"),
    [
        ({"column": "idea", "broken": True}, True, False, "broken"),
        ({"column": "idea"}, False, False, "idle"),
        ({"column": "idea"}, True, False, "working"),
        ({"column": "idea"}, True, True, "waiting"),
        ({"column": "build", "plan_status": "building"}, False, False, "working"),
        ({"column": "build", "plan_status": "queued"}, False, False, "working"),
        ({"column": "build", "plan_status": "building"}, False, True, "waiting"),
        ({"column": "build", "plan_status": "failed"}, False, False, "failed"),
        ({"column": "build", "plan_status": "interrupted"}, False, False, "interrupted"),
        ({"column": "review", "plan_status": "failed"}, False, False, "idle"),
        ({"column": "review", "plan_status": "interrupted"}, False, False, "idle"),
        ({"column": "idea", "open_questions": 1}, False, False, "waiting"),
        ({"column": "plan", "plan_status": "draft"}, False, False, "waiting"),
        ({"column": "plan", "plan_status": "approved"}, False, False, "idle"),
        ({"column": "review", "review": "done"}, False, False, "waiting"),
        ({"column": "review", "review": "running"}, False, False, "idle"),
        ({"column": "done"}, False, False, "idle"),
        ({"column": "build", "plan_status": "failed", "open_questions": 1}, False, False, "failed"),
    ],
)
def test_the_tile_state_comes_from_the_facts(overrides, streaming, approval, expected) -> None:
    assert card_state(facts(**overrides), streaming, approval) == expected


@pytest.mark.parametrize(
    ("status", "expected"),
    [("running", "running"), ("done", "done"), ("skipped", "none"), ("failed", "none"), (None, "none"), ("x", "none")],
)
def test_a_review_row_status_maps_to_the_flows_review_state(status, expected) -> None:
    assert review_from_status(status) == expected
