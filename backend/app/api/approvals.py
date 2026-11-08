"""The user's decision on an action an agent run waits for."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.agent.runs import RunRegistry

router = APIRouter(tags=["approvals"])


class ApprovalDecision(BaseModel):
    decision: Literal["approve", "deny"]
    # Why, in the user's words (optional); kept on the call's step, where learning proposals read it.
    reason: str | None = Field(default=None, max_length=500)


@router.post("/approvals/{approval_id}", status_code=status.HTTP_204_NO_CONTENT)
def decide_approval(approval_id: str, payload: ApprovalDecision, request: Request) -> Response:
    registry: RunRegistry = request.app.state.runs
    if not registry.decide(approval_id, payload.decision, payload.reason):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This approval is no longer open; the run may have ended.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
