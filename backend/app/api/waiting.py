"""*Waiting for me*: what waits for the user across the projects of an environment, and a card per project with its
board on.

Thin: the route asks the waiting service (app/board/waiting_facts.py) and returns its answer. Titles and summaries
come from cards, plans and tool calls as stored; the frontend shows them as plain text.
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from app.board.waiting import WaitingAction, WaitingKind
from app.board.waiting_facts import ProjectSummary, WaitingOverview, WaitingService

router = APIRouter(tags=["board"])


class WaitingItemOut(BaseModel):
    kind: WaitingKind
    project_id: int | None
    card_id: int | None
    conversation_id: int | None
    model: str | None
    since: datetime | None
    action: WaitingAction
    title: str
    detail: str
    approval_id: str | None
    plan_id: int | None


class WaitingProjectOut(BaseModel):
    project_id: int
    name: str
    # The current version (the first open one by name) and its progress; None without an open version.
    version: str | None
    done: int
    total: int
    building: int
    blocked: int
    escalated: int
    cost_month: float


class WaitingOut(BaseModel):
    items: list[WaitingItemOut]
    projects: list[WaitingProjectOut]
    # The server's time of this answer: sent back as `since` after the user looked.
    now: datetime


def project_out(summary: ProjectSummary) -> WaitingProjectOut:
    return WaitingProjectOut(
        project_id=summary.project_id,
        name=summary.name,
        version=summary.version,
        done=summary.done,
        total=summary.total,
        building=summary.building,
        blocked=summary.blocked,
        escalated=summary.escalated,
        cost_month=summary.cost_month,
    )


def waiting_out(overview: WaitingOverview) -> WaitingOut:
    return WaitingOut(
        items=[
            WaitingItemOut(
                kind=item.kind,
                project_id=item.project_id,
                card_id=item.card_id,
                conversation_id=item.conversation_id,
                model=item.model,
                since=item.since,
                action=item.action,
                title=item.title,
                detail=item.detail,
                approval_id=item.approval_id,
                plan_id=item.plan_id,
            )
            for item in overview.items
        ],
        projects=[project_out(summary) for summary in overview.projects],
        now=overview.now,
    )


def waiting_service(request: Request) -> WaitingService:
    return WaitingService(request.app.state.session_factory, request.app.state.runs)


WaitingDependency = Annotated[WaitingService, Depends(waiting_service)]


@router.get("/waiting", response_model=WaitingOut)
def read_waiting(
    waiting: WaitingDependency,
    environment_id: Annotated[int | None, Query(ge=1)] = None,
    since: Annotated[datetime | None, Query()] = None,
) -> WaitingOut:
    """Plans to approve, reviews to judge, questions, approvals, interrupted builds and the Inbox cards new since
    `since` (when the user last looked; kept by the browser), in `environment_id`'s projects or in all."""
    return waiting_out(waiting.overview(environment_id, since))
