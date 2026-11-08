"""Learning from the work: a conversation's review, the project's proposals with their measurement,
accepting and declining them, and the checks of the rules (one rule's difference, contradictions across levels)."""

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.conversations import ConversationDependency, utility_context
from app.api.dependencies import ProjectDependency, SessionDependency
from app.learning.measuring import (
    Contradiction,
    check_rule,
    find_contradictions,
    measurement,
    project_rules,
    start_measuring,
)
from app.learning.proposals import RuleView
from app.learning.service import LearningError, LearningService, ProposalNotFound, open_proposals
from app.storage.models import LearningProposal

router = APIRouter(tags=["learning"])

Level = Literal["global", "environment", "project"]
ProposalKind = Literal["rule", "memory", "skill_step", "rule_removal"]
ProposalSource = Literal["utility", "fallback", "check"]
MeasurementStatus = Literal["ready", "measuring", "measured", "not_measured", "not_needed"]
Effect = Literal["better", "worse", "same"]
SignalKind = Literal["declined", "test_failed_twice", "known_search"]


class ProposalSignalOut(BaseModel):
    step_id: int
    signal: SignalKind
    tool: str
    summary: str
    note: str | None


class MeasurementOut(BaseModel):
    status: MeasurementStatus
    note: str | None
    pass_rate_without: float | None
    pass_rate_with: float | None
    effect: Effect | None
    # The comparison's eval runs: the normal setup and the variant with the change.
    baseline_run_id: int | None
    variant_run_id: int | None


class LearningProposalOut(BaseModel):
    id: int
    conversation_id: int | None
    kind: ProposalKind
    level: Level
    target: str
    text: str
    diff: str
    rationale: str
    source: ProposalSource
    branch: str | None
    signals: list[ProposalSignalOut]
    measurement: MeasurementOut
    created_at: datetime


class RuleIO(BaseModel):
    level: Level
    path: str = Field(max_length=200)
    rule: str = Field(max_length=500)


class ContradictionOut(BaseModel):
    rules: list[RuleIO]
    explanation: str


class ContradictionReportOut(BaseModel):
    # False when no utility model was available to check.
    available: bool
    contradictions: list[ContradictionOut]


def learning(request: Request) -> LearningService:
    state = request.app.state
    return LearningService(utility_context(request), state.blueprint_dir, state.workspace_dir)


def signal_out(value: dict[str, Any]) -> ProposalSignalOut | None:
    try:
        return ProposalSignalOut.model_validate(value)
    except ValueError:
        return None


def proposal_out(session: Session, proposal: LearningProposal) -> LearningProposalOut:
    measured = measurement(session, proposal)
    signals = [signal for signal in (signal_out(value) for value in proposal.signals or []) if signal is not None]
    return LearningProposalOut(
        id=proposal.id,
        conversation_id=proposal.conversation_id,
        kind=proposal.kind,  # type: ignore[arg-type]  # stored as one of ProposalKind
        level=proposal.level,  # type: ignore[arg-type]  # stored as one of Level
        target=proposal.target,
        text=proposal.text,
        diff=proposal.diff,
        rationale=proposal.rationale,
        source=proposal.source,  # type: ignore[arg-type]  # stored as one of ProposalSource
        branch=proposal.branch,
        signals=signals,
        measurement=MeasurementOut(
            status=measured.status,
            note=measured.note,
            pass_rate_without=measured.pass_rate_without,
            pass_rate_with=measured.pass_rate_with,
            effect=measured.effect,
            baseline_run_id=proposal.baseline_run_id,
            variant_run_id=proposal.variant_run_id,
        ),
        created_at=proposal.created_at,
    )


def rule_io(rule: RuleView) -> RuleIO:
    return RuleIO(level=rule.level, path=rule.path, rule=rule.rule)


def contradiction_out(item: Contradiction) -> ContradictionOut:
    return ContradictionOut(rules=[rule_io(rule) for rule in item.rules], explanation=item.explanation)


def learning_error(error: LearningError) -> HTTPException:
    if isinstance(error, ProposalNotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(error))
    return HTTPException(status.HTTP_409_CONFLICT, str(error))


@router.post("/conversations/{conversation_id}/learning-review", response_model=list[LearningProposalOut])
async def review_conversation(
    conversation: ConversationDependency, session: SessionDependency, request: Request
) -> list[LearningProposalOut]:
    """Look at the conversation's signals and messages now; returns the proposals it made (none is applied)."""
    if conversation.project_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Learning proposals are made for project conversations.")
    conversation_id = conversation.id
    session.close()
    created = await learning(request).review(conversation_id)
    with request.app.state.session_factory() as fresh:
        return [proposal_out(fresh, fresh.get_one(LearningProposal, item.id)) for item in created]


@router.get("/projects/{project_id}/learning/proposals", response_model=list[LearningProposalOut])
def list_proposals(project: ProjectDependency, session: SessionDependency) -> list[LearningProposalOut]:
    """The proposals waiting for the user, oldest first, each with its measurement as it stands."""
    return [proposal_out(session, item) for item in open_proposals(session, project.id)]


@router.post("/projects/{project_id}/learning/proposals/{proposal_id}/measure", response_model=LearningProposalOut)
def measure_proposal(
    proposal_id: int, project: ProjectDependency, session: SessionDependency, request: Request
) -> LearningProposalOut:
    """Start the comparison of the project's eval cases without and with the proposal (three plays per case)."""
    project_id = project.id
    session.close()
    try:
        start_measuring(learning(request), request.app.state.evals, project_id, proposal_id)
    except LearningError as error:
        raise learning_error(error) from error
    with request.app.state.session_factory() as fresh:
        return proposal_out(fresh, fresh.get_one(LearningProposal, proposal_id))


@router.post("/projects/{project_id}/learning/proposals/{proposal_id}/accept", status_code=status.HTTP_204_NO_CONTENT)
def accept_proposal(proposal_id: int, project: ProjectDependency, request: Request) -> Response:
    """Apply the proposal: one commit in its Blueprint layer (its branch is removed) or in the project memory."""
    try:
        learning(request).accept(project.id, proposal_id)
    except LearningError as error:
        raise learning_error(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/projects/{project_id}/learning/proposals/{proposal_id}/decline", status_code=status.HTTP_204_NO_CONTENT)
def decline_proposal(proposal_id: int, project: ProjectDependency, request: Request) -> Response:
    """Leave the layer unchanged and remove the proposal's branch."""
    try:
        learning(request).decline(project.id, proposal_id)
    except LearningError as error:
        raise learning_error(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/projects/{project_id}/learning/rules", response_model=list[RuleIO])
def list_rules(project: ProjectDependency, request: Request) -> list[RuleIO]:
    """The rules of the standards of every level the project sees, with the level and file that define each."""
    return [rule_io(rule) for rule in project_rules(learning(request), project.id)]


@router.post("/projects/{project_id}/learning/rule-checks", response_model=LearningProposalOut)
def check_project_rule(
    payload: RuleIO, project: ProjectDependency, session: SessionDependency, request: Request
) -> LearningProposalOut:
    """A proposal to remove one rule, measured at once when the project has eval cases: whether it makes a
    difference."""
    project_id = project.id
    session.close()
    service = learning(request)
    try:
        proposal = check_rule(service, project_id, RuleView(payload.level, payload.path, payload.rule))
        if proposal.measurement == "ready":
            start_measuring(service, request.app.state.evals, project_id, proposal.id)
    except LearningError as error:
        raise learning_error(error) from error
    with request.app.state.session_factory() as fresh:
        return proposal_out(fresh, fresh.get_one(LearningProposal, proposal.id))


@router.post("/projects/{project_id}/learning/contradictions", response_model=ContradictionReportOut)
async def check_contradictions(
    project: ProjectDependency, session: SessionDependency, request: Request
) -> ContradictionReportOut:
    """Rules of different levels that contradict each other, by the utility model; a report, nothing is changed."""
    project_id = project.id
    session.close()
    report = await find_contradictions(learning(request), project_id)
    return ContradictionReportOut(
        available=report.available, contradictions=[contradiction_out(item) for item in report.contradictions]
    )
