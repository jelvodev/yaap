"""After a restart a building plan is interrupted, and Continue goes on from the first open step."""

import shutil
import stat
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.plans.models  # noqa: F401 - registers the plans table on the shared metadata
from app.git.place import git_place
from app.git.service import commit_all, run_git
from app.plans.model import PlanStatus
from app.plans.recover import recover_builds
from app.plans.runner import RESTARTED
from app.plans.service import BuildStart, PlanService
from app.storage.db import create_session_factory
from app.storage.models import Base
from tests.test_plan_runner import Setup, executor, models, setup, step, worker_makes  # noqa: F401
from tests.test_plan_service import arguments

START = BuildStart("main", "a" * 40, "yaap/plan-1-x", ".yaap/builds/1", 0)


@pytest.fixture
def factory(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    engine = create_engine(f"sqlite:///{(tmp_path / 'plans.db').as_posix()}")
    Base.metadata.create_all(engine)
    yield create_session_factory(engine)
    engine.dispose()


def test_recovery_interrupts_building_plans_and_reopens_their_working_step(factory):
    service = PlanService(factory)
    view = service.approve_for_build(service.submit(1, 10, arguments(steps=3)).id, START)
    steps = (
        replace(view.plan.steps[0], status="done", commit="c" * 40, note="Made it."),
        replace(view.plan.steps[1], status="working"),
        view.plan.steps[2],
    )
    service.save_progress(view.id, replace(view.plan, steps=steps), view.build)

    with factory() as session:
        assert recover_builds(session) == 1

    recovered = service.get(view.id)
    assert recovered.status == "interrupted" and recovered.failure == RESTARTED
    assert [item.status for item in recovered.plan.steps] == ["done", "open", "open"]
    assert recovered.plan.steps[0].commit == "c" * 40 and recovered.build_folder == ".yaap/builds/1"


def test_recovery_leaves_plans_in_other_statuses_alone(factory):
    service = PlanService(factory)
    draft = service.submit(1, 10, arguments())
    done = service.end_build(service.approve_for_build(service.submit(1, 11, arguments()).id, START).id, "all_steps_done")
    failed = service.end_build(service.approve_for_build(service.submit(1, 12, arguments()).id, START).id, "stop", "x")
    with factory() as session:
        assert recover_builds(session) == 0
    statuses: dict[int, PlanStatus] = {view.id: service.get(view.id).status for view in (draft, done, failed)}
    assert statuses == {draft.id: "draft", done.id: "done", failed.id: "failed"}


def test_recovery_with_nothing_building_changes_nothing(factory):
    with factory() as session:
        assert recover_builds(session) == 0


def test_continue_after_a_restart_resumes_at_the_open_step_in_the_same_copy(setup, fake, executor, monkeypatch):  # noqa: F811
    plan_id = setup.submit([step("One"), step("Two")])
    builds = setup.client.app.state.builds
    with monkeypatch.context() as patch:
        patch.setattr(builds, "launch", lambda plan_id: None)
        assert setup.client.post(f"/api/v1/plans/{plan_id}/approve").status_code == 200
    # Step 1 is done in the copy and committed, as a build that ran for a while would have left it.
    service = PlanService(setup.client.app.state.session_factory)
    view = service.get(plan_id)
    copy = setup.folder / view.build_folder
    (copy / "one.txt").write_text("one.txt\n", encoding="utf-8")
    place = git_place(1, copy)
    commit_all(place, "Step 1: One")
    commit = run_git(["rev-parse", "HEAD"], place).stdout.strip()
    done = replace(view.plan.steps[0], status="done", commit=commit, note="Made one.txt.")
    working = replace(view.plan.steps[1], status="working")
    service.save_progress(plan_id, replace(view.plan, steps=(done, working)), view.build)

    with setup.client.app.state.session_factory() as session:
        recover_builds(session)
    assert setup.plan(plan_id)["status"] == "interrupted"
    assert setup.plan(plan_id)["steps"][1]["status"] == "open"

    fake.responses.append(worker_makes("two.txt"))
    resumed = setup.client.post(f"/api/v1/plans/{plan_id}/continue")
    assert resumed.status_code == 200, resumed.text
    plan = setup.wait_for(plan_id)

    assert plan["status"] == "done", plan
    assert models(fake) == ["claude-sonnet-5-5"]  # only the open step was worked on
    assert plan["steps"][0]["commit"] == commit and len(plan["steps"][1]["commit"]) == 40
    assert plan["build_folder"] == view.build_folder
    log = run_git(["log", "--format=%s"], place).stdout.split("\n")[:3]
    assert log == ["Step 2: Two", "Step 1: One", "First"]
    assert "Made one.txt." in setup.client.get(f"/api/v1/plans/{plan_id}").text


def test_continue_is_refused_when_the_copy_is_gone(setup, fake, executor, monkeypatch):  # noqa: F811
    plan_id = setup.submit([step("One")])
    with monkeypatch.context() as patch:
        patch.setattr(setup.client.app.state.builds, "launch", lambda plan_id: None)
        setup.client.post(f"/api/v1/plans/{plan_id}/approve")
    with setup.client.app.state.session_factory() as session:
        recover_builds(session)
    def writable(function, path, _error):
        Path(path).chmod(stat.S_IWRITE)
        function(path)

    shutil.rmtree(setup.folder / setup.plan(plan_id)["build_folder"], onerror=writable)
    response = setup.client.post(f"/api/v1/plans/{plan_id}/continue")
    assert response.status_code == 409 and response.json()["detail"]["code"] == "not_resumable"
    assert setup.plan(plan_id)["status"] == "interrupted"
