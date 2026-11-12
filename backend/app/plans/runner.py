"""The build of an approved plan, run by code step by step.

Approving makes the copy (plans/workspace.py) and marks the plan building; the build then runs in the background.
Per open step: the step is `working`; its brief goes to the worker through the brief attempt loop (worker, the worker
again with the failure, then the main model; agent/brief_delegation.py), or to the main model when the step has no
check YAAP can run; YAAP runs the step's other checks itself; a passing step is committed in the copy ("Step N:
<title>") and is `done` with its commit and the worker's note. A failing step fails the plan with the reason. After
the last step every check runs once more on the final tree (kept as evidence), and the build branch is brought into
the project. A stopped or failed build brings what it has, so partial work is reachable as a branch.

Nothing here decides who may run what: every tool call and every check goes through the build run's permission gate.
"""

import asyncio
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace

import anyio

from app.agent.brief_delegation import BriefOutcome, delegate_brief_once, run_check
from app.agent.runs import PendingApproval
from app.agent.sub_run_report import parse_report
from app.core.background import BackgroundWork
from app.core.reply_services import ReplyServices
from app.git.common import GitError
from app.git.place import git_place
from app.git.service import Repository, commit_all, run_git
from app.plans.build import build_branch_name, numbered_branch, remaining_checks, step_brief, step_commit_message
from app.plans.build_run import BuildRun, close_build_run, open_build_run, plan_commands
from app.plans.model import Plan, PlanEvent, PlanStep
from app.plans.progress import (
    FinalCheck,
    capped_note,
    capped_output,
    earlier_notes,
    open_steps,
    with_attempts,
    with_step,
)
from app.plans.service import BuildRefused, BuildStart, PlanListeners, PlanNotFound, PlanService, PlanView
from app.plans.workspace import (
    BuildCopyError,
    copy_relative,
    deliver_build,
    make_build_copy,
    read_base,
    remove_build_copy,
)
from app.projects.service import project_path
from app.storage.models import Project

logger = logging.getLogger(__name__)

STOPPED = "Stopped. The steps done so far are on the build branch."
RESTARTED = "YAAP restarted while this plan was being built. Continue to go on from the last step done."


@dataclass(frozen=True)
class StepResult:
    passed: bool
    # Why it did not pass: the failing check's output or the model's report.
    detail: str
    note: str


@dataclass
class BuildRunner:
    """Starts, runs and stops builds; one per project (checked under `lock` when a build starts)."""

    services: Callable[[], ReplyServices]
    listeners: PlanListeners = field(default_factory=PlanListeners)
    work: BackgroundWork[None] = field(default_factory=BackgroundWork)
    lock: threading.Lock = field(default_factory=threading.Lock)
    tasks: dict[int, "asyncio.Task[None]"] = field(default_factory=dict)
    running: dict[int, BuildRun] = field(default_factory=dict)
    # Work the listeners start when a build ends (the current-state refresh).
    follow_ups: BackgroundWork[bool] = field(default_factory=BackgroundWork)
    # Set while YAAP shuts down: a build cut off then is interrupted, not stopped, and Continue goes on with it.
    shutting_down: bool = False

    def plans(self) -> PlanService:
        return PlanService(self.services().session_factory)

    # --- Starting (worker thread), then launching (event loop) ----------------------------------------------------

    def approve(self, plan_id: int) -> PlanView:
        """Make the copy and mark the draft building. Blocking: call it from a worker thread, then `launch`."""
        services = self.services()
        plans = PlanService(services.session_factory)
        view = plans.get(plan_id)
        if view.status != "draft":
            raise BuildRefused("plan_not_draft", f"Plan {plan_id} is {view.status}; only a draft can be approved.")
        with services.session_factory() as session:
            project = session.get_one(Project, view.project_id)
            folder = project_path(services.workspace_dir, project)
            environment_id = project.environment_id
        if not (folder / ".git").exists():
            raise BuildRefused(
                "no_repository", "Building needs a git repository. Create one in the project's Branches panel."
            )
        with self.lock:
            plans.refuse_second_build(view.project_id, plan_id)
            try:
                place = git_place(environment_id, folder)
                base = read_base(place)
                taken = run_git(["for-each-ref", "--format=%(refname:short)", "refs/heads"], place).stdout.split()
                branch = numbered_branch(build_branch_name(plan_id, view.card_id, view.plan.title), taken)
                make_build_copy(place, lambda path: git_place(environment_id, path), plan_id, base.commit, branch)
            except (GitError, BuildCopyError) as error:
                raise BuildRefused("copy_failed", str(error)) from error
            start = BuildStart(base.branch, base.commit, branch, copy_relative(plan_id), base.uncommitted)
            try:
                return plans.approve_for_build(plan_id, start)
            except BaseException:
                remove_build_copy(folder, plan_id)
                raise

    def resume(self, plan_id: int) -> PlanView:
        """Mark an interrupted or failed build building again; its copy must still be there. Then `launch`."""
        services = self.services()
        plans = PlanService(services.session_factory)
        view = plans.get(plan_id)
        with services.session_factory() as session:
            folder = project_path(services.workspace_dir, session.get_one(Project, view.project_id))
        if view.build_folder and not (folder / view.build_folder / ".git").is_dir():
            raise BuildRefused("not_resumable", "The build's copy is gone, so it cannot go on. Make a new plan.")
        with self.lock:
            return plans.continue_build(plan_id)

    def launch(self, plan_id: int) -> None:
        """Run the build in the background. On the event loop."""
        task = self.work.start(self.run(plan_id), name=f"build-{plan_id}")
        self.tasks[plan_id] = task
        task.add_done_callback(lambda _task: self.tasks.pop(plan_id, None))

    async def stop(self, plan_id: int) -> PlanView:
        """Stop the build: it ends failed, with the steps done so far delivered. On the event loop."""
        task = self.tasks.get(plan_id)
        if task is not None:
            self.work.cancel(task)
            await self.work.wait(task)
        plans = self.plans()
        view = plans.get(plan_id)
        if view.status == "building":  # Nothing ran it (a start that never got going).
            view = plans.end_build(plan_id, "stop", STOPPED)
        return view

    async def shutdown(self) -> None:
        """Cut off running builds as a restart would; they are interrupted and go on with Continue."""
        self.shutting_down = True
        await self.work.stop()
        await self.follow_ups.stop()

    def waiting_approvals(self, plan_id: int) -> list[PendingApproval]:
        """The approvals the build and its steps wait for now."""
        build = self.running.get(plan_id)
        if build is None:
            return []
        token = build.run.token
        runs = self.services().runs.runs.values()
        return [
            approval
            for run in runs
            if run.token == token or run.parent_token == token
            for approval in run.state.approvals.values()
            if not approval.future.done()
        ]

    # --- Running ---------------------------------------------------------------------------------------------------

    async def run(self, plan_id: int) -> None:
        services = self.services()
        plans = PlanService(services.session_factory)
        try:
            view = plans.get(plan_id)
            build = await anyio.to_thread.run_sync(open_build_run, services, view)
        except (BuildRefused, PlanNotFound) as error:
            await self.end(services, None, plan_id, "step_failed", str(error))
            return
        except Exception as error:  # noqa: BLE001 - a background build that cannot start ends failed with the reason
            logger.exception("The build of plan %s could not start.", plan_id)
            await self.end(services, None, plan_id, "step_failed", f"The build could not start: {error}")
            return
        self.running[plan_id] = build
        plans.set_build_run(plan_id, build.run.state.record_id)
        try:
            failure = await self.run_steps(plans, build, view)
        except asyncio.CancelledError:
            with anyio.CancelScope(shield=True):
                if self.shutting_down:
                    await self.end(services, build, plan_id, "restart_lost", RESTARTED)
                else:
                    await self.end(services, build, plan_id, "stop", STOPPED)
            raise
        except Exception as error:  # noqa: BLE001 - a background build ends failed with the reason, YAAP goes on
            logger.exception("The build of plan %s stopped on an error.", plan_id)
            await self.end(services, build, plan_id, "step_failed", f"The build stopped on an error: {error}")
            return
        if failure is not None:
            await self.end(services, build, plan_id, "step_failed", failure)
            return
        await self.final_checks(plans, build, plan_id)
        await self.end(services, build, plan_id, "all_steps_done", None)

    async def run_steps(self, plans: PlanService, build: BuildRun, view: PlanView) -> str | None:
        """Every open step in order; the reason of the first that fails, or None when all are done."""
        copy = await anyio.to_thread.run_sync(git_place, build.environment_id, build.copy_folder)
        for open_step in open_steps(view.plan):
            current = plans.get(view.id)
            plan, info = with_step(current.plan, open_step.number, status="working"), current.build
            plans.save_progress(view.id, plan, info)
            step = next(step for step in plan.steps if step.number == open_step.number)
            brief = step_brief(plan, step, earlier_notes(plan, step.number))
            first = build.first if brief.check else "main"
            outcome = await delegate_brief_once(build.delegator, build.run, brief, first)
            result = await self.judge(build, step, outcome)
            info = with_attempts(info, step.number, tuple(role for role, _, _ in outcome.history))
            attempts = len(info.attempts[step.number])
            if not result.passed:
                plan = with_step(plan, step.number, status="failed", note=result.note, attempts=attempts)
                plans.save_progress(view.id, plan, info)
                return f"Step {step.number} ({step.title}) failed: {result.detail}"
            previous = last_commit(plan) or current.base_commit or ""
            branch = current.build_branch or ""
            commit = await anyio.to_thread.run_sync(commit_step, copy, branch, step, previous)
            plan = with_step(plan, step.number, status="done", note=result.note, commit=commit, attempts=attempts)
            plans.save_progress(view.id, plan, info)
        return None

    async def judge(self, build: BuildRun, step: PlanStep, outcome: BriefOutcome) -> StepResult:
        """Whether the step passed: its check (and the checks the brief could not hold) as YAAP ran them, or, without
        any check YAAP can run, the main model's report."""
        report = parse_report(outcome.last.full_report)
        note = capped_note(report.conclusion or outcome.last.report)
        if not any(check.run for check in step.checks):
            done = outcome.last.status == "complete" and report.status == "done"
            detail = "" if done else note or f"The main model's attempt ended {outcome.last.status}."
            return StepResult(done, detail, note)
        if outcome.check is None:
            return StepResult(False, f"The attempt ended {outcome.last.status} before its check ran.", note)
        if not outcome.passed:
            return StepResult(False, check_detail(outcome.check.command, outcome.check.output), note)
        for command in remaining_checks(step):
            check = await run_check(build.delegator.runs, build.run, command)
            if check.outcome != "passed":
                return StepResult(False, check_detail(command, check.output), note)
        return StepResult(True, "", note)

    async def final_checks(self, plans: PlanService, build: BuildRun, plan_id: int) -> None:
        """Every check of the plan once more on the finished tree, kept as evidence; a failure here fails no step."""
        view = plans.get(plan_id)
        checks: list[FinalCheck] = []
        for command in plan_commands(view.plan):
            result = await run_check(build.delegator.runs, build.run, command)
            checks.append(FinalCheck(command, result.outcome, capped_output(result.output)))
        info = replace(view.build, final_checks=tuple(checks))
        plans.save_progress(plan_id, view.plan, info)

    async def end(
        self, services: ReplyServices, build: BuildRun | None, plan_id: int, event: PlanEvent, failure: str | None
    ) -> None:
        """Close the build run, bring the branch into the project, set the plan's status and tell the listeners."""
        plans = PlanService(services.session_factory)
        if build is not None:
            self.running.pop(plan_id, None)
            close_build_run(services, build, "complete" if event == "all_steps_done" else "incomplete")
            # An interrupted build delivers when it ends; until then its copy holds the work.
            problem = None
            if event != "restart_lost":
                problem = await anyio.to_thread.run_sync(self.deliver, plans, build, plan_id)
            if problem is not None:
                event = "step_failed" if event == "all_steps_done" else event
                failure = f"{failure} {problem}" if failure else problem
        try:
            plans.end_build(plan_id, event, failure)
        except PlanNotFound:
            return  # The conversation, and the plan with it, was deleted: the branch stays, the copy is pruned.
        self.listeners.tell(plan_id, event)

    def deliver(self, plans: PlanService, build: BuildRun, plan_id: int) -> str | None:
        """Bring the build branch into the project; the reason when it could not. Blocking."""
        try:
            view = plans.get(plan_id)
        except PlanNotFound:
            return None
        commit = last_commit(view.plan) or view.base_commit
        if not view.build_branch or not view.base_commit or commit is None:
            return None
        info = view.build
        try:
            place = git_place(build.environment_id, build.project_folder)
            copy = git_place(build.environment_id, build.copy_folder)
            brought = deliver_build(place, copy, view.build_branch, view.base_commit, commit, info.delivered)
        except (GitError, BuildCopyError) as error:
            return f"The branch could not be brought into the project: {error}"
        if brought:
            plans.save_progress(plan_id, view.plan, replace(info, delivered=commit))
        return None


def check_detail(command: str, output: str) -> str:
    return f"`{command}` did not pass.\n{capped_output(output)}"


def last_commit(plan: Plan) -> str | None:
    """The commit of the last step that made one."""
    commits = [step.commit for step in plan.steps if step.status == "done" and step.commit]
    return commits[-1] if commits else None


def commit_step(copy: Repository, branch: str, step: PlanStep, previous: str) -> str | None:
    """Commit what the step changed in the copy, on the build branch; the new commit, or None when it changed
    nothing. Blocking."""
    current = run_git(["symbolic-ref", "--quiet", "--short", "HEAD"], copy, accept_exit_codes=(0, 1)).stdout.strip()
    if current != branch:
        raise GitError(f"The copy is no longer on the build branch {branch}; the step's work was not committed.")
    try:
        commit_all(copy, step_commit_message(step))
    except GitError as error:
        if "nothing to commit" not in str(error):
            raise
    tip = run_git(["rev-parse", "HEAD"], copy).stdout.strip()
    return tip if tip != previous else None
