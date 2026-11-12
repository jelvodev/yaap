"""A second opinion on one reply of a conversation: what it would be and cost, ask for it, read the ones given.

The opinion is folded under the reply; it is not part of the conversation, so the model never sees it unless the
person puts it in the composer. Starting answers with the review as `running`; the page reads the conversation's
second opinions again until it has ended.
"""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select

from app.api.conversations import ConversationDependency
from app.api.dependencies import SessionDependency
from app.api.reviews import ReviewOut, review_out
from app.core.reply_services import ReplyServices
from app.reviews.second_opinion import OpinionPreview, SecondOpinionRefused, SecondOpinions
from app.reviews.service import ReviewService
from app.storage.models import Message, Review

router = APIRouter(tags=["second opinion"])

OPINIONS_SHOWN = 200


class SecondOpinionStartIn(BaseModel):
    # Agree to a reviewer of the writer's own vendor, when no other vendor can be used.
    allow_same_vendor: bool = False


class EstimateOut(BaseModel):
    input_tokens: int
    output_tokens: int
    # The most it can cost; empty on a subscription, which is not paid per token.
    usd: float | None


class SecondOpinionPreviewOut(BaseModel):
    # changes: the reply's changes are reviewed; answer: the reply changed nothing, its answer is checked.
    kind: Literal["changes", "answer"]
    available: bool
    reviewer_provider: str | None
    reviewer_model: str | None
    reviewer_label: str | None
    same_vendor: bool
    # Why no other vendor's model is used, or why no model can be.
    reason: str | None
    estimate: EstimateOut | None


def preview_out(preview: OpinionPreview) -> SecondOpinionPreviewOut:
    estimate = preview.estimate
    return SecondOpinionPreviewOut(
        kind=preview.kind,
        available=preview.available,
        reviewer_provider=preview.reviewer_provider,
        reviewer_model=preview.reviewer_model,
        reviewer_label=preview.reviewer_label,
        same_vendor=preview.same_vendor,
        reason=preview.reason,
        estimate=EstimateOut(input_tokens=estimate.input_tokens, output_tokens=estimate.output_tokens, usd=estimate.usd)
        if estimate is not None
        else None,
    )


def second_opinions(request: Request) -> SecondOpinions:
    return SecondOpinions(ReviewService(ReplyServices.from_state(request.app.state)))


def reply_of(session: SessionDependency, conversation_id: int, message_id: int) -> Message:
    """The assistant reply of this conversation; anything else is not found."""
    message = session.get(Message, message_id)
    if message is None or message.conversation_id != conversation_id or message.role != "assistant":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Reply not found in this conversation.")
    return message


@router.get(
    "/conversations/{conversation_id}/messages/{message_id}/second-opinion", response_model=SecondOpinionPreviewOut
)
def preview_second_opinion(
    message_id: int, request: Request, conversation: ConversationDependency, session: SessionDependency
) -> SecondOpinionPreviewOut:
    """Who would give a second opinion on the reply, on what, and the most it would cost."""
    reply_of(session, conversation.id, message_id)
    try:
        return preview_out(second_opinions(request).preview_for_reply(conversation.id, message_id))
    except SecondOpinionRefused as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error


@router.post(
    "/conversations/{conversation_id}/messages/{message_id}/second-opinion",
    response_model=ReviewOut,
    status_code=status.HTTP_201_CREATED,
)
def start_second_opinion(
    message_id: int,
    payload: SecondOpinionStartIn,
    request: Request,
    conversation: ConversationDependency,
    session: SessionDependency,
) -> ReviewOut:
    """Ask for a second opinion on the reply; refused with the reason when no model can give it."""
    reply_of(session, conversation.id, message_id)
    try:
        review_id = second_opinions(request).start_for_reply(conversation.id, message_id, payload.allow_same_vendor)
    except SecondOpinionRefused as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    session.expire_all()
    return review_out(session, session.get_one(Review, review_id))


@router.get("/conversations/{conversation_id}/second-opinions", response_model=list[ReviewOut])
def list_second_opinions(conversation: ConversationDependency, session: SessionDependency) -> list[ReviewOut]:
    """The second opinions given in this conversation, newest first."""
    query = (
        select(Review)
        .where(Review.conversation_id == conversation.id, Review.message_id.is_not(None))
        .order_by(Review.id.desc())
        .limit(OPINIONS_SHOWN)
    )
    return [review_out(session, review) for review in session.scalars(query)]
