"""Behind the scenes (tracing): the labels under one reply and the reasons of its tool calls."""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.api.conversations import ConversationDependency
from app.api.dependencies import SessionDependency
from app.core.why import WhyOut, why_out
from app.storage.models import Message
from app.tracing.behind import BehindLabel, LabelKind, ReplyBehind, reply_behind
from app.tracing.notes import ApprovalOutcome, CallWhy

router = APIRouter(tags=["tracing"])


class BehindLabelOut(BaseModel):
    kind: LabelKind
    text: str
    detail: str | None
    why: WhyOut | None
    cost: float | None
    tokens: int | None
    items: list[str]
    run_id: int | None
    step_id: int | None


class CallWhyOut(BaseModel):
    call_key: str | None
    tool: str | None
    approval: ApprovalOutcome | None
    why: WhyOut


class BehindOut(BaseModel):
    labels: list[BehindLabelOut]
    calls: list[CallWhyOut]


def label_out(label: BehindLabel) -> BehindLabelOut:
    return BehindLabelOut(
        kind=label.kind,
        text=label.text,
        detail=label.detail,
        why=why_out(label.why) if label.why is not None else None,
        cost=label.cost,
        tokens=label.tokens,
        items=list(label.items),
        run_id=label.run_id,
        step_id=label.step_id,
    )


def call_out(call: CallWhy) -> CallWhyOut:
    return CallWhyOut(call_key=call.call_key, tool=call.tool, approval=call.approval, why=why_out(call.why))


def behind_out(behind: ReplyBehind | None) -> BehindOut:
    if behind is None:
        return BehindOut(labels=[], calls=[])
    return BehindOut(labels=[label_out(label) for label in behind.labels], calls=[call_out(c) for c in behind.calls])


@router.get("/conversations/{conversation_id}/messages/{message_id}/behind", response_model=BehindOut)
def read_behind(conversation: ConversationDependency, message_id: int, session: SessionDependency) -> BehindOut:
    """The labels behind a reply (model, memory, helpers, approvals, checks, cache) with the reason of each."""
    message = session.get(Message, message_id)
    if message is None or message.conversation_id != conversation.id or message.role != "assistant":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Reply not found in this conversation.")
    return behind_out(reply_behind(session, conversation.id, message_id))
