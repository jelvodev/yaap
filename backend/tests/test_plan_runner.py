"""A build runs the plan's steps with a scripted worker and a fake executor: a passing step is committed, a failing
check retries on the worker and then on the main model, a step with no command goes to the main model, and the final
check runs on the finished tree."""

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from app.plans.service import PlanService
from app.providers.fake_provider import ScriptedResponse
from app.targets.base import CommandResult
from tests.test_commands_and_skills import start
from tests.test_plan_workspace import host_git

REPORT = "Status: done\nConclusion: {what}\nChanged files: {file}\nLeft open: nothing"
WORKER, MAIN, UTILITY = "claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"


@dataclass
class FakeExecutor:
    """Runs no command: answers with an exit code by command, and keeps what it was asked."""

    failing: set[str] = field(default_factory=set)
    commands: list[str] = field(default_factory=list)

    async def run(self, command, environment, timeout, session, silence=0, on_output=None):
        self.commands.append(command)
        failed = any(marker in command for marker in self.failing)
        return CommandResult(1 if failed else 0, "check failed: assertion" if failed else "check passed")


@pytest.fixture
def executor(monkeypatch: pytest.MonkeyPatch) -> FakeExecutor:
    fake_executor = FakeExecutor()
    monkeypatch.setattr("app.agent.command_tools.executor", lambda run: fake_executor)
    return fake_executor


@dataclass
class Setup:
    client: Any
    project: dict
    folder: Path
    conversation_id: int

    def submit(self, steps: list[dict]) -> int:
        service = PlanService(self.client.app.state.session_factory)
        view = service.submit(self.project["id"], self.conversation_id, {"title": "Two files", "steps": steps})
        return view.id

    def plan(self, plan_id: int) -> dict:
        return self.client.get(f"/api/v1/plans/{plan_id}").json()

    def approve(self, plan_id: int) -> dict:
        response = self.client.post(f"/api/v1/plans/{plan_id}/approve")
        assert response.status_code == 200, response.text
        return self.wait_for(plan_id)

    def wait_for(self, plan_id: int, statuses: tuple[str, ...] = ("done", "failed", "interrupted")) -> dict:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            plan = self.plan(plan_id)
            if plan["status"] in statuses:
                return plan
            time.sleep(0.1)
        raise AssertionError(f"The build did not end: {self.plan(plan_id)}")


@pytest.fixture
def setup(client, tmp_path) -> Setup:
    project = client.post("/api/v1/environments/1/projects", json={"name": "Site"}).json()
    conversation_id = start(client, project_id=project["id"], preset="Balanced")
    folder = Path(tmp_path) / "workspace" / project["folder"]
    (folder / "app.py").write_text("print('hi')\n", encoding="utf-8")
    host_git("add", "-A", cwd=folder)
    host_git("commit", "-m", "First", cwd=folder)
    return Setup(client, project, folder, conversation_id)


def models(fake) -> list[str]:
    """The models of the steps' attempts, without the utility call that refreshes the project's state afterwards."""
    return [request.model for request in fake.requests if request.model != UTILITY]


def step(title: str, *checks: dict) -> dict:
    return {"title": title, "files": [f"{title.lower()}.txt"], "checks": list(checks) or [{"text": "done", "run": "verify"}]}


def worker_makes(name: str, report_status: str = "done") -> ScriptedResponse:
    report = REPORT.format(what=f"Made {name}.", file=name).replace("Status: done", f"Status: {report_status}")
    return ScriptedResponse(text=report, tool_calls=(("write_file", {"path": name, "content": f"{name}\n"}),))


def copy_log(setup: Setup, plan: dict) -> list[str]:
    copy = setup.folder / plan["build_folder"]
    return host_git("log", "--format=%s", cwd=copy).split("\n")[:-1]


def test_passing_steps_are_committed_in_the_copy_and_the_branch_comes_into_the_project(setup, fake, executor):
    fake.responses.extend([worker_makes("one.txt"), worker_makes("two.txt")])
    plan_id = setup.submit([step("One"), step("Two", {"text": "also", "run": "verify two"})])
    main_head = host_git("rev-parse", "HEAD", cwd=setup.folder)

    plan = setup.approve(plan_id)

    assert plan["status"] == "done", plan
    assert [item["status"] for item in plan["steps"]] == ["done", "done"]
    assert all(len(item["commit"]) == 40 for item in plan["steps"])
    assert plan["steps"][0]["note"] == "Made one.txt."
    assert copy_log(setup, plan)[:2] == ["Step 2: Two", "Step 1: One"]
    # Each step's own file is in its own commit, in the copy only.
    assert host_git("show", "--name-only", "--format=", plan["steps"][1]["commit"], cwd=setup.folder).strip() == "two.txt"
    assert not (setup.folder / "one.txt").exists()
    # Only the build branch came into the project, at the last step's commit; the checked-out branch is as it was.
    assert host_git("rev-parse", plan["build_branch"], cwd=setup.folder).strip() == plan["steps"][1]["commit"]
    assert host_git("symbolic-ref", "--short", "HEAD", cwd=setup.folder).strip() == "main"
    assert host_git("rev-parse", "HEAD", cwd=setup.folder) == main_head
    assert plan["build"]["delivered"] is True
    assert models(fake) == [WORKER, WORKER]


def test_every_check_runs_once_more_on_the_finished_tree(setup, fake, executor):
    fake.responses.extend([worker_makes("one.txt"), worker_makes("two.txt")])
    plan_id = setup.submit([step("One"), step("Two", {"text": "also", "run": "verify two"})])

    plan = setup.approve(plan_id)

    finals = plan["build"]["final_checks"]
    assert [(check["command"], check["outcome"]) for check in finals] == [("verify", "passed"), ("verify two", "passed")]
    # Step 1's check, step 2's checks, then the final run of each command once.
    assert executor.commands == ["verify", "verify two", "verify", "verify two"]


def test_a_failing_check_retries_on_the_worker_then_on_the_main_model_and_then_fails_the_plan(setup, fake, executor):
    executor.failing = {"bad"}
    fake.responses.extend([worker_makes("one.txt")] + [worker_makes("two.txt")] * 3)
    plan_id = setup.submit(
        [step("One", {"text": "fine", "run": "good"}), step("Two", {"text": "broken", "run": "bad"}), step("Three")]
    )

    plan = setup.approve(plan_id)

    assert plan["status"] == "failed"
    assert [item["status"] for item in plan["steps"]] == ["done", "failed", "open"]
    assert "Step 2 (Two) failed" in plan["failure"] and "`bad` did not pass" in plan["failure"]
    assert "check failed: assertion" in plan["failure"]
    assert models(fake) == [WORKER, WORKER, WORKER, MAIN]
    assert plan["build"]["attempts"] == {"1": ["worker"], "2": ["worker", "worker", "main"]}
    assert plan["steps"][1]["attempts"] == 3 and plan["steps"][1]["commit"] is None
    # The partial work is reachable as a branch: the first step's commit, none for the failed step.
    assert host_git("rev-parse", plan["build_branch"], cwd=setup.folder).strip() == plan["steps"][0]["commit"]
    assert copy_log(setup, plan) == ["Step 1: One", "First"]
    assert plan["build"]["delivered"] is True


def test_a_check_that_fails_once_passes_on_the_retry_and_the_step_is_done(setup, fake, executor):
    calls: list[str] = []
    original = executor.run

    async def flaky(command, environment, timeout, session, silence=0, on_output=None):
        calls.append(command)
        executor.failing = {"verify"} if len(calls) == 1 else set()
        return await original(command, environment, timeout, session, silence, on_output)

    executor.run = flaky  # type: ignore[method-assign] - the dataclass instance's own method, replaced for this test
    fake.responses.extend([worker_makes("one.txt"), worker_makes("one.txt")])
    plan_id = setup.submit([step("One")])

    plan = setup.approve(plan_id)

    assert plan["status"] == "done"
    assert plan["build"]["attempts"]["1"] == ["worker", "worker"]
    assert models(fake) == [WORKER, WORKER]


def test_a_step_without_a_command_goes_to_the_main_model_and_is_done_when_it_reports_done(setup, fake, executor):
    fake.responses.append(worker_makes("one.txt"))
    plan_id = setup.submit([step("One", {"text": "A person looks at it"})])

    plan = setup.approve(plan_id)

    assert plan["status"] == "done", plan
    assert models(fake) == [MAIN]
    assert plan["steps"][0]["status"] == "done" and len(plan["steps"][0]["commit"]) == 40
    assert executor.commands == []
    assert plan["build"]["final_checks"] == []


def test_a_step_without_a_command_fails_when_the_main_model_does_not_report_done(setup, fake, executor):
    fake.responses.append(worker_makes("one.txt", report_status="blocked"))
    plan_id = setup.submit([step("One", {"text": "A person looks at it"})])

    plan = setup.approve(plan_id)

    assert plan["status"] == "failed" and plan["steps"][0]["status"] == "failed"
    assert plan["steps"][0]["commit"] is None


def test_stopping_a_build_in_a_step_ends_it_failed_with_the_reason_and_leaves_the_copy(setup, fake, executor):
    fake.responses.append(ScriptedResponse(text="Working " * 100, delay=0.5))
    plan_id = setup.submit([step("One")])
    assert setup.client.post(f"/api/v1/plans/{plan_id}/approve").status_code == 200
    deadline = time.monotonic() + 30
    while setup.plan(plan_id)["steps"][0]["status"] != "working":
        assert time.monotonic() < deadline, "the step never started"
        time.sleep(0.1)

    stopped = setup.client.post(f"/api/v1/plans/{plan_id}/stop")

    assert stopped.status_code == 200
    plan = setup.wait_for(plan_id)
    assert plan["status"] == "failed" and plan["failure"].startswith("Stopped.")
    assert (setup.folder / plan["build_folder"] / ".git").is_dir()


def test_stopping_a_finished_build_answers_with_the_plan_as_it_is(setup, fake, executor):
    fake.responses.append(worker_makes("one.txt"))
    plan = setup.approve(setup.submit([step("One")]))
    assert plan["status"] == "done"
    stopped = setup.client.post(f"/api/v1/plans/{plan['id']}/stop")
    assert stopped.status_code == 200 and stopped.json()["status"] == "done"
