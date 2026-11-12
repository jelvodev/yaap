"""Plans the agent handed over with submit_plan: read one, edit a draft, discard it, approve it to build, continue
or stop its build.

The plan card in the chat reads the plan by its id. An edit replaces the title, summary, risks and the whole step
list of a draft; a plan that is not valid answers 422 with code `invalid_plan` and every error. A build that cannot
start or go on answers 409 with a `code` (plans/service.BuildRefusal) and a message to show.
"""

from datetime import datetime
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.agent.escalation import AttemptOutcome, Role
from app.plans.model import PlanStatus, StepStatus
from app.plans.runner import BuildRunner
from app.plans.service import (
    BuildRefusal,
    BuildRefused,
    InvalidPlan,
    PlanConflict,
    PlanNotFound,
    PlanService,
    PlanView,
)

router = APIRouter(tags=["plans"])


class StepCheckIn(BaseModel):
    text: str
    # One command line YAAP runs to prove the step; empty when a person or the model judges it.
    run: str | None = None


class PlanStepIn(BaseModel):
    title: str
    details: str = ""
    files: list[str] = Field(default_factory=list)
    checks: list[StepCheckIn] = Field(default_factory=list)


class PlanEditIn(BaseModel):
    title: str
    summary: str = ""
    risks: list[str] = Field(default_factory=list)
    steps: list[PlanStepIn]


class StepCheckOut(BaseModel):
    text: str
    run: str | None


class PlanStepOut(BaseModel):
    number: int
    title: str
    details: str
    files: list[str]
    checks: list[StepCheckOut]
    status: StepStatus
    note: str
    # The step's commit in the build's copy (the full id).
    commit: str | None
    attempts: int


class FinalCheckOut(BaseModel):
    command: str
    outcome: AttemptOutcome
    output: str


class BuildApprovalOut(BaseModel):
    approval_id: str
    tool: str
    summary: str


class PlanBuildOut(BaseModel):
    """The build of an approved plan, beside its steps' state."""

    # Uncommitted changes the project had when the build started: they are not part of it.
    uncommitted: int
    # What the steps' workers and takeovers cost so far.
    cost: float | None
    # Per step number: who took each attempt, in order.
    attempts: dict[str, list[Role]]
    # Every check of the plan once more on the finished tree.
    final_checks: list[FinalCheckOut]
    # Whether the build branch was brought into the project.
    delivered: bool
    # What the build waits for now; answered with POST /approvals/{approval_id}.
    approvals: list[BuildApprovalOut]


class BuildRefusalOut(BaseModel):
    code: BuildRefusal
    message: str


class PlanOut(BaseModel):
    id: int
    project_id: int
    conversation_id: int
    card_id: int | None
    status: PlanStatus
    title: str
    summary: str
    risks: list[str]
    steps: list[PlanStepOut]
    # What was accepted but is worth saying, such as a plan without a check YAAP can run.
    warnings: list[str]
    failure: str | None
    created_at: datetime
    approved_at: datetime | None
    finished_at: datetime | None
    # Where the build starts and works: the project's branch (None on a detached HEAD) and commit, the build branch
    # and its folder relative to the project folder.
    base_branch: str | None = None
    base_commit: str | None = None
    build_branch: str | None = None
    build_folder: str | None = None
    # None until the plan is approved.
    build: PlanBuildOut | None = None
    # The card's acceptance criteria commands that approving also approves (a draft: as the card has them now),
    # run on the build's copy after the build for the review's evidence.
    criteria_commands: list[str] = []


def plan_service(request: Request) -> PlanService:
    return PlanService(request.app.state.session_factory)


def build_runner(request: Request) -> BuildRunner:
    runner: BuildRunner = request.app.state.builds
    return runner


PlanServiceDependency = Annotated[PlanService, Depends(plan_service)]
BuildRunnerDependency = Annotated[BuildRunner, Depends(build_runner)]


def build_out(view: PlanView, plans: PlanService, runner: BuildRunner) -> PlanBuildOut | None:
    if view.approved_at is None:
        return None
    info = view.build
    return PlanBuildOut(
        uncommitted=info.uncommitted,
        cost=plans.build_cost(view),
        attempts={str(number): list(roles) for number, roles in info.attempts.items()},
        final_checks=[
            FinalCheckOut(command=check.command, outcome=check.outcome, output=check.output)
            for check in info.final_checks
        ],
        delivered=info.delivered is not None,
        approvals=[
            BuildApprovalOut(approval_id=approval.id, tool=approval.tool, summary=approval.summary)
            for approval in runner.waiting_approvals(view.id)
        ],
    )


def plan_out(view: PlanView, plans: PlanService, runner: BuildRunner) -> PlanOut:
    plan = view.plan
    return PlanOut(
        id=view.id,
        project_id=view.project_id,
        conversation_id=view.conversation_id,
        card_id=view.card_id,
        status=view.status,
        title=plan.title,
        summary=plan.summary,
        risks=list(plan.risks),
        steps=[
            PlanStepOut(
                number=step.number,
                title=step.title,
                details=step.details,
                files=list(step.files),
                checks=[StepCheckOut(text=check.text, run=check.run) for check in step.checks],
                status=step.status,
                note=step.note,
                commit=step.commit,
                attempts=step.attempts,
            )
            for step in plan.steps
        ],
        warnings=view.warnings,
        failure=view.failure,
        created_at=view.created_at,
        approved_at=view.approved_at,
        finished_at=view.finished_at,
        base_branch=view.base_branch,
        base_commit=view.base_commit,
        build_branch=view.build_branch,
        build_folder=view.build_folder,
        build=build_out(view, plans, runner),
        criteria_commands=list(approved_criteria(view, runner)),
    )


def approved_criteria(view: PlanView, runner: BuildRunner) -> tuple[str, ...]:
    if view.status == "draft":
        return runner.approved_extras(view)
    return view.build.criteria_commands


def not_found(error: PlanNotFound) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, str(error))


def refused(error: BuildRefused) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, {"code": error.code, "message": str(error)})


def invalid(error: InvalidPlan) -> HTTPException:
    detail = {"code": "invalid_plan", "message": "The plan is not valid.", "errors": error.errors}
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail)


@router.get("/plans/{plan_id}", response_model=PlanOut)
def get_plan(plan_id: int, plans: PlanServiceDependency, runner: BuildRunnerDependency) -> PlanOut:
    try:
        return plan_out(plans.get(plan_id), plans, runner)
    except PlanNotFound as error:
        raise not_found(error) from error


@router.patch("/plans/{plan_id}", response_model=PlanOut)
def edit_plan(
    plan_id: int, payload: PlanEditIn, plans: PlanServiceDependency, runner: BuildRunnerDependency
) -> PlanOut:
    """Replace a draft plan; only a draft can be edited."""
    try:
        return plan_out(plans.edit(plan_id, payload.model_dump()), plans, runner)
    except PlanNotFound as error:
        raise not_found(error) from error
    except PlanConflict as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except InvalidPlan as error:
        raise invalid(error) from error


@router.post("/plans/{plan_id}/discard", response_model=PlanOut)
def discard_plan(plan_id: int, plans: PlanServiceDependency, runner: BuildRunnerDependency) -> PlanOut:
    try:
        return plan_out(plans.discard(plan_id), plans, runner)
    except PlanNotFound as error:
        raise not_found(error) from error
    except PlanConflict as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error


@router.post(
    "/plans/{plan_id}/approve",
    response_model=PlanOut,
    responses={409: {"model": BuildRefusalOut}, 422: {"description": "The plan is not valid (code invalid_plan)."}},
)
async def approve_plan(plan_id: int, plans: PlanServiceDependency, runner: BuildRunnerDependency) -> PlanOut:
    """Approve a draft and start its build in a copy of the project. Approving approves the plan's check commands,
    exactly as shown, for this build."""
    try:
        view = await anyio.to_thread.run_sync(runner.approve, plan_id)
    except PlanNotFound as error:
        raise not_found(error) from error
    except BuildRefused as error:
        raise refused(error) from error
    except PlanConflict as error:
        raise refused(BuildRefused("plan_not_draft", str(error))) from error
    except InvalidPlan as error:
        raise invalid(error) from error
    runner.launch(plan_id)
    return plan_out(view, plans, runner)


@router.post("/plans/{plan_id}/continue", response_model=PlanOut, responses={409: {"model": BuildRefusalOut}})
async def continue_plan(plan_id: int, plans: PlanServiceDependency, runner: BuildRunnerDependency) -> PlanOut:
    """Go on with an interrupted or failed build from its first open step, in the same copy."""
    try:
        view = await anyio.to_thread.run_sync(runner.resume, plan_id)
    except PlanNotFound as error:
        raise not_found(error) from error
    except BuildRefused as error:
        raise refused(error) from error
    except PlanConflict as error:
        raise refused(BuildRefused("not_resumable", str(error))) from error
    runner.launch(plan_id)
    return plan_out(view, plans, runner)


@router.post("/plans/{plan_id}/stop", response_model=PlanOut)
async def stop_plan(plan_id: int, plans: PlanServiceDependency, runner: BuildRunnerDependency) -> PlanOut:
    """Stop a build: it ends failed, and the steps done so far come into the project as the build branch."""
    try:
        return plan_out(await runner.stop(plan_id), plans, runner)
    except PlanNotFound as error:
        raise not_found(error) from error
    except PlanConflict as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
