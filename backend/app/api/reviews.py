"""Reviews by another vendor of a project's changes: start one, list them, read one.

A review of the uncommitted changes starts from the changes view, a review of a conversation's changes from the
conversation's heading; goals and scheduled tasks that changed code start their own. The model call runs in the
background, so starting answers with the review as `running`; the panel reads it again until it has ended.
"""

from datetime import datetime
from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import select

from app.api.dependencies import ProjectDependency, SessionDependency
from app.core.reply_services import ReplyServices
from app.reviews.report import Severity
from app.reviews.scope import NothingToReview, ReviewSource
from app.reviews.service import ReviewService, ReviewStatus
from app.storage.models import Conversation, Review

router = APIRouter(tags=["reviews"])

REVIEWS_SHOWN = 50


class ReviewStartIn(BaseModel):
    # The conversation whose changes to review; empty reviews the project's uncommitted changes.
    conversation_id: int | None = None


class FindingOut(BaseModel):
    path: str
    line: int | None
    severity: Severity
    explanation: str


class ReviewSummaryOut(BaseModel):
    id: int
    project_id: int | None
    conversation_id: int | None
    message_id: int | None
    conversation_title: str | None
    goal_id: int | None
    source: ReviewSource
    status: ReviewStatus
    reason: str | None
    writer_vendors: list[str]
    reviewer_provider: str | None
    reviewer_model: str | None
    # The reviewer is of the writer's own vendor, as no other vendor was allowed.
    same_vendor: bool
    # An answer check: whether the reviewer agrees; empty when it does not say or the review is of changes.
    agrees: bool | None
    findings_count: int
    high_count: int
    cost: float | None
    created_at: datetime
    finished_at: datetime | None


class ReviewOut(ReviewSummaryOut):
    objective: str | None
    summary: str | None
    # An answer check that does not agree: what the reviewer would answer instead.
    alternative: str | None
    notes: list[str]
    findings: list[FindingOut]
    diff: str
    input_tokens: int
    output_tokens: int


def summary_fields(review: Review, title: str | None) -> dict[str, object]:
    findings = review.findings or []
    return {
        "id": review.id,
        "project_id": review.project_id,
        "conversation_id": review.conversation_id,
        "message_id": review.message_id,
        "conversation_title": title,
        "goal_id": review.goal_id,
        "source": review.source,
        "status": review.status,
        "reason": review.reason,
        "writer_vendors": review.writer_vendors or [],
        "reviewer_provider": review.reviewer_provider,
        "reviewer_model": review.reviewer_model,
        "same_vendor": review.same_vendor,
        "agrees": review.agrees,
        "findings_count": len(findings),
        "high_count": sum(1 for finding in findings if finding.get("severity") == "high"),
        "cost": review.cost,
        "created_at": review.created_at,
        "finished_at": review.finished_at,
    }


def review_out(session: SessionDependency, review: Review) -> ReviewOut:
    conversation = session.get(Conversation, review.conversation_id) if review.conversation_id else None
    return ReviewOut.model_validate(
        {
            **summary_fields(review, conversation.title if conversation else None),
            "objective": review.objective,
            "summary": review.summary,
            "alternative": review.alternative,
            "notes": review.notes or [],
            "findings": review.findings or [],
            "diff": review.diff,
            "input_tokens": review.input_tokens,
            "output_tokens": review.output_tokens,
        }
    )


@router.post("/projects/{project_id}/reviews", response_model=ReviewOut, status_code=status.HTTP_201_CREATED)
def start_review(
    payload: ReviewStartIn, request: Request, project: ProjectDependency, session: SessionDependency
) -> ReviewOut:
    """Start a review of the project's uncommitted changes, or of what one of its conversations changed."""
    if payload.conversation_id is not None:
        conversation = session.get(Conversation, payload.conversation_id)
        if conversation is None or conversation.project_id != project.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found in this project.")
    reviews = ReviewService(ReplyServices.from_state(request.app.state))
    try:
        if payload.conversation_id is not None:
            review_id = reviews.start_for_conversation(payload.conversation_id)
        else:
            review_id = reviews.start_for_changes(project.id)
    except NothingToReview as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    session.expire_all()
    return review_out(session, session.get_one(Review, review_id))


@router.get("/projects/{project_id}/reviews", response_model=list[ReviewSummaryOut])
def list_reviews(
    project: ProjectDependency,
    session: SessionDependency,
    conversation_id: int | None = None,
    limit: int = Query(default=REVIEWS_SHOWN, ge=1, le=200),
) -> list[ReviewSummaryOut]:
    """The project's reviews, newest first; with a conversation, only its own."""
    query = (
        select(Review, Conversation.title)
        .outerjoin(Conversation, Conversation.id == Review.conversation_id)
        .where(Review.project_id == project.id, Review.message_id.is_(None))
        .order_by(Review.id.desc())
        .limit(limit)
    )
    if conversation_id is not None:
        query = query.where(Review.conversation_id == conversation_id)
    return [ReviewSummaryOut.model_validate(summary_fields(review, title)) for review, title in session.execute(query)]


@router.get("/projects/{project_id}/reviews/{review_id}", response_model=ReviewOut)
def read_review(review_id: int, project: ProjectDependency, session: SessionDependency) -> ReviewOut:
    review = session.get(Review, review_id)
    if review is None or review.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Review not found.")
    return review_out(session, review)

