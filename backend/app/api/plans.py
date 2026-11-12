"""Plans the agent handed over with submit_plan: read one, edit a draft, discard it.

The plan card in the chat reads the plan by its id. An edit replaces the title, summary, risks and the whole step
list of a draft; a plan that is not valid answers 422 with code `invalid_plan` and every error.
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.plans.model import PlanStatus, StepStatus
from app.plans.service import InvalidPlan, PlanConflict, PlanNotFound, PlanService, PlanView

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
    commit: str | None
    attempts: int


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


def plan_service(request: Request) -> PlanService:
    return PlanService(request.app.state.session_factory)


PlanServiceDependency = Annotated[PlanService, Depends(plan_service)]


def plan_out(view: PlanView) -> PlanOut:
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
    )


def not_found(error: PlanNotFound) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, str(error))


@router.get("/plans/{plan_id}", response_model=PlanOut)
def get_plan(plan_id: int, plans: PlanServiceDependency) -> PlanOut:
    try:
        return plan_out(plans.get(plan_id))
    except PlanNotFound as error:
        raise not_found(error) from error


@router.patch("/plans/{plan_id}", response_model=PlanOut)
def edit_plan(plan_id: int, payload: PlanEditIn, plans: PlanServiceDependency) -> PlanOut:
    """Replace a draft plan; only a draft can be edited."""
    try:
        return plan_out(plans.edit(plan_id, payload.model_dump()))
    except PlanNotFound as error:
        raise not_found(error) from error
    except PlanConflict as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except InvalidPlan as error:
        detail = {"code": "invalid_plan", "message": "The plan is not valid.", "errors": error.errors}
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail) from error


@router.post("/plans/{plan_id}/discard", response_model=PlanOut)
def discard_plan(plan_id: int, plans: PlanServiceDependency) -> PlanOut:
    try:
        return plan_out(plans.discard(plan_id))
    except PlanNotFound as error:
        raise not_found(error) from error
    except PlanConflict as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
