"""The plan service on a database of its own: drafts, replanning, edits, discards and the build's status changes."""

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.plans.models  # noqa: F401 - registers the plans table on the shared metadata
from app.plans.model import PlanEvent, PlanStatus
from app.plans.progress import BuildInfo
from app.plans.service import (
    BuildRefused,
    BuildStart,
    InvalidPlan,
    PlanConflict,
    PlanListeners,
    PlanNotFound,
    PlanService,
)
from app.storage.db import create_session_factory
from app.storage.models import Base

START = BuildStart("main", "a" * 40, "yaap/plan-1-x", ".yaap/builds/1", 2, ("pytest -q",))


def arguments(title: str = "Health endpoint", steps: int = 2) -> dict[str, Any]:
    return {
        "title": title,
        "summary": "Add /health.",
        "steps": [
            {"title": f"Step {number}", "files": [f"f{number}.py"], "checks": [{"text": "ok", "run": "pytest -q"}]}
            for number in range(1, steps + 1)
        ],
    }


@pytest.fixture
def factory(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    engine = create_engine(f"sqlite:///{(tmp_path / 'plans.db').as_posix()}")
    Base.metadata.create_all(engine)
    yield create_session_factory(engine)
    engine.dispose()


@pytest.fixture
def service(factory: sessionmaker[Session]) -> PlanService:
    return PlanService(factory)


def status_of(service: PlanService, plan_id: int) -> PlanStatus:
    return service.get(plan_id).status


def test_submit_stores_a_draft_with_its_steps_and_warnings(service):
    view = service.submit(1, 10, arguments())
    assert (view.status, view.project_id, view.conversation_id) == ("draft", 1, 10)
    assert [step.title for step in view.plan.steps] == ["Step 1", "Step 2"]
    assert view.warnings == [] and view.build_run_id is None
    assert service.get(view.id).plan == view.plan


def test_submit_of_an_invalid_plan_raises_every_error_and_stores_nothing(service):
    bad = {"title": "", "steps": [{"title": "A", "checks": []}]}
    with pytest.raises(InvalidPlan) as raised:
        service.submit(1, 10, bad)
    assert len(raised.value.errors) == 2
    with pytest.raises(PlanNotFound):
        service.get(1)


def test_a_new_plan_cancels_the_other_drafts_of_its_conversation_only(service):
    first = service.submit(1, 10, arguments("First"))
    other_conversation = service.submit(1, 11, arguments("Elsewhere"))
    second = service.submit(1, 10, arguments("Second"))
    assert status_of(service, first.id) == "cancelled"
    assert status_of(service, second.id) == "draft"
    assert status_of(service, other_conversation.id) == "draft"


def test_listeners_hear_a_submit_and_one_that_fails_does_not_stop_the_others(factory):
    heard: list[tuple[int, PlanEvent]] = []
    listeners = PlanListeners()

    def broken(plan_id: int, event: PlanEvent) -> None:
        raise RuntimeError("listener failed")

    listeners.add(broken)
    listeners.add(lambda plan_id, event: heard.append((plan_id, event)))
    view = PlanService(factory, listeners).submit(1, 10, arguments())
    assert heard == [(view.id, "submit")]


def test_edit_replaces_the_whole_step_list_of_a_draft(service):
    view = service.submit(1, 10, arguments())
    edited = service.edit(view.id, arguments("Renamed", steps=3))
    assert edited.status == "draft" and edited.plan.title == "Renamed" and len(edited.plan.steps) == 3
    assert service.get(view.id).plan == edited.plan


def test_edit_with_an_invalid_plan_keeps_the_draft_as_it_was(service):
    view = service.submit(1, 10, arguments())
    with pytest.raises(InvalidPlan):
        service.edit(view.id, {"title": "x", "steps": []})
    assert service.get(view.id).plan == view.plan


def test_edit_of_a_discarded_plan_is_a_conflict(service):
    view = service.submit(1, 10, arguments())
    service.discard(view.id)
    with pytest.raises(PlanConflict):
        service.edit(view.id, arguments())


def test_discard_cancels_a_draft_once(service):
    view = service.submit(1, 10, arguments())
    discarded = service.discard(view.id)
    assert discarded.status == "cancelled" and discarded.finished_at is not None
    with pytest.raises(PlanConflict):
        service.discard(view.id)


@pytest.mark.parametrize("call", ["get", "discard", "continue_build"])
def test_an_unknown_plan_is_not_found(service, call):
    with pytest.raises(PlanNotFound):
        getattr(service, call)(99)


def test_approve_for_build_starts_the_build_and_records_where(service):
    view = service.submit(1, 10, arguments())
    built = service.approve_for_build(view.id, START)
    assert built.status == "building" and built.approved_at is not None
    assert (built.base_branch, built.base_commit) == ("main", "a" * 40)
    assert (built.build_branch, built.build_folder) == ("yaap/plan-1-x", ".yaap/builds/1")
    assert built.build.uncommitted == 2 and built.build.criteria_commands == ("pytest -q",)
    assert [item.id for item in service.building(1)] == [view.id]
    assert service.building(2) == []


@pytest.mark.parametrize("prepare", ["discard", "build"])
def test_approve_for_build_refuses_a_plan_that_is_not_a_draft(service, prepare):
    view = service.submit(1, 10, arguments())
    if prepare == "discard":
        service.discard(view.id)
    else:
        service.approve_for_build(view.id, START)
    with pytest.raises(BuildRefused) as raised:
        service.approve_for_build(view.id, START)
    assert raised.value.code == "plan_not_draft"


@pytest.mark.parametrize(
    ("event", "failure", "status"),
    [("all_steps_done", None, "done"), ("step_failed", "A check failed.", "failed"), ("stop", None, "failed")],
)
def test_end_build_moves_a_building_plan_to_its_final_status(service, event, failure, status):
    view = service.approve_for_build(service.submit(1, 10, arguments()).id, START)
    ended = service.end_build(view.id, event, failure)
    assert ended.status == status and ended.failure == failure and ended.finished_at is not None


def test_end_build_cuts_a_long_failure_and_refuses_a_plan_that_is_not_building(service):
    view = service.approve_for_build(service.submit(1, 10, arguments()).id, START)
    assert len(service.end_build(view.id, "step_failed", "x" * 5000).failure or "") == 1000
    with pytest.raises(PlanConflict):
        service.end_build(view.id, "all_steps_done")


def test_continue_reopens_the_failed_step_and_keeps_the_done_ones(service):
    view = service.approve_for_build(service.submit(1, 10, arguments(steps=3)).id, START)
    plan = view.plan
    steps = (
        replace(plan.steps[0], status="done", commit="c" * 40),
        replace(plan.steps[1], status="failed"),
        plan.steps[2],
    )
    service.save_progress(view.id, replace(plan, steps=steps), view.build)
    service.end_build(view.id, "step_failed", "no")
    resumed = service.continue_build(view.id)
    assert resumed.status == "building" and resumed.failure is None and resumed.finished_at is None
    assert [step.status for step in resumed.plan.steps] == ["done", "open", "open"]


def test_continue_is_refused_for_a_draft_and_for_a_build_whose_copy_is_gone(service):
    draft = service.submit(1, 10, arguments())
    with pytest.raises(BuildRefused) as raised:
        service.continue_build(draft.id)
    assert raised.value.code == "not_resumable"
    view = service.approve_for_build(service.submit(1, 11, arguments()).id, START)
    service.end_build(view.id, "stop")
    service.build_gone(view.id)
    with pytest.raises(BuildRefused):
        service.continue_build(view.id)


def test_a_queued_plan_waits_then_starts_once(service):
    view = service.submit(1, 10, arguments())
    queued = service.queue_for_build(view.id, "yaap/plan-1-x", BuildInfo(queued="All 2 build slots are in use."))
    assert queued.status == "queued" and queued.build.queued == "All 2 build slots are in use."
    service.set_queue_reason(view.id, "Waits for #3.")
    assert service.get(view.id).build.queued == "Waits for #3."
    started = service.start_queued(view.id, START)
    assert started is not None and started.status == "building" and started.build.queued is None
    assert service.start_queued(view.id, START) is None


def test_set_queue_reason_does_nothing_once_the_plan_is_not_queued(service):
    view = service.approve_for_build(service.submit(1, 10, arguments()).id, START)
    service.set_queue_reason(view.id, "late")
    assert service.get(view.id).build.queued is None


def test_queued_plans_come_earliest_approved_first_across_projects(service):
    ids = [service.submit(project, 10 + project, arguments()).id for project in (1, 2, 3)]
    for plan_id in (ids[1], ids[0], ids[2]):
        service.queue_for_build(plan_id, "yaap/x", BuildInfo(queued="wait"))
    assert [view.id for view in service.queued()] == [ids[1], ids[0], ids[2]]


def test_build_run_and_progress_are_stored_with_the_plan(service):
    view = service.approve_for_build(service.submit(1, 10, arguments()).id, START)
    service.set_build_run(view.id, 7)
    first = replace(view.plan.steps[0], status="done", note="Made it.")
    plan = replace(view.plan, steps=(first, *view.plan.steps[1:]))
    service.save_progress(view.id, plan, BuildInfo(delivered="d" * 40))
    stored = service.get(view.id)
    assert stored.build_run_id == 7 and stored.build.delivered == "d" * 40
    assert stored.plan.steps[0].note == "Made it."


def test_build_cost_is_unknown_before_approval_and_zero_without_usage(service):
    draft = service.submit(1, 10, arguments())
    assert service.build_cost(draft) is None
    assert service.build_cost(service.approve_for_build(draft.id, START)) == 0.0
