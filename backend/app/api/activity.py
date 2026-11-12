"""Working comfort around a conversation: the message queue, steering and waiting approvals."""

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.agent.runs import RunRegistry
from app.api.conversations import (
    ConversationDependency,
    ReplyServiceDependency,
    event_stream,
    reply_http_error,
)
from app.api.dependencies import (
    EnvironmentDependency,
    SessionDependency,
)
from app.core.message_queue import (
    MAX_MESSAGE_ATTACHMENTS,
    MAX_MESSAGE_LENGTH,
    MAX_QUEUED_MESSAGES,
    QueueError,
    add_steering,
    request_send_now,
    queued_items,
)
from app.core.queue_commands import QueueItemKind, queue_item_kind
from app.core.reply_errors import ReplyError
from app.core.reply_service import ReplyService
from app.core.replies import ReplyRegistry
from app.storage.models import Attachment, QueuedMessage

router = APIRouter(tags=["activity"])

MAX_QUEUED = MAX_QUEUED_MESSAGES


class QueuedMessageIn(BaseModel):
    text: str = Field(default="", max_length=MAX_MESSAGE_LENGTH)
    attachment_ids: list[int] = Field(default_factory=list, max_length=MAX_MESSAGE_ATTACHMENTS)


class QueuedMessageOut(BaseModel):
    id: int
    text: str
    attachment_ids: list[int]
    created_at: datetime
    send_now_pending: bool
    steering_pending: bool
    kind: QueueItemKind


class QueueTake(BaseModel):
    # One item by id, or all of them joined into one message.
    item_id: int | None = None
    all: bool = False


class QueueTaken(BaseModel):
    text: str
    attachment_ids: list[int]


class QueueSend(BaseModel):
    item_id: int | None = None
    all: bool = False


class QueueSendNowOut(BaseModel):
    status: Literal["stopping"]


@router.post(
    "/conversations/{conversation_id}/queue/send-now",
    response_model=QueueSendNowOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def send_queued_now(
    payload: QueueSend,
    conversation: ConversationDependency,
    session: SessionDependency,
    service: ReplyServiceDependency,
    request: Request,
) -> QueueSendNowOut:
    """Persist the selection, then stop exactly the reply it was meant to follow."""
    job = request.app.state.replies.running(conversation.id)
    if job is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "No reply is running; send the queued message directly.")
    try:
        request_send_now(session, conversation.id, payload.item_id, payload.all, job.id)
    except QueueError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    service.schedule_send_now(conversation.id, job.id)
    request.app.state.replies.cancel_job(conversation.id, job.id)
    return QueueSendNowOut(status="stopping")


def queued_to_out(item: QueuedMessage) -> QueuedMessageOut:
    return QueuedMessageOut(
        id=item.id,
        text=item.text,
        attachment_ids=item.attachment_ids or [],
        created_at=item.created_at,
        send_now_pending=item.send_now_job_id is not None,
        steering_pending=item.steering_run_id is not None,
        kind=queue_item_kind(item.text),
    )


@router.get("/conversations/{conversation_id}/queue", response_model=list[QueuedMessageOut])
def list_queue(conversation: ConversationDependency, session: SessionDependency) -> list[QueuedMessageOut]:
    return [queued_to_out(item) for item in queued_items(session, conversation.id)]


@router.post("/conversations/{conversation_id}/queue", response_model=QueuedMessageOut, status_code=status.HTTP_201_CREATED)
def add_to_queue(payload: QueuedMessageIn, conversation: ConversationDependency, session: SessionDependency) -> QueuedMessageOut:
    """Keep a message for when the running reply ends."""
    if not payload.text.strip() and not payload.attachment_ids:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A queued message needs text or an attachment.")
    if payload.attachment_ids and queue_item_kind(payload.text) != "message":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A command cannot carry attachments.")
    items = queued_items(session, conversation.id)
    if len(items) >= MAX_QUEUED:
        raise HTTPException(status.HTTP_409_CONFLICT, f"At most {MAX_QUEUED} messages can wait in the queue.")
    if payload.attachment_ids:
        owned = set(
            session.scalars(
                select(Attachment.id).where(
                    Attachment.id.in_(payload.attachment_ids),
                    Attachment.conversation_id == conversation.id,
                    Attachment.message_id.is_(None),
                )
            )
        )
        if owned != set(payload.attachment_ids):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "An attachment does not belong to this conversation.")
    item = QueuedMessage(
        conversation_id=conversation.id,
        text=payload.text.strip(),
        attachment_ids=payload.attachment_ids,
        position=(max((existing.position for existing in items), default=0) + 1),
        created_at=datetime.now(UTC),
    )
    session.add(item)
    session.commit()
    return queued_to_out(item)


@router.delete("/conversations/{conversation_id}/queue/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_from_queue(item_id: int, conversation: ConversationDependency, session: SessionDependency) -> Response:
    item = session.get(QueuedMessage, item_id)
    if item is None or item.conversation_id != conversation.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That message is not in the queue.")
    session.delete(item)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/conversations/{conversation_id}/queue/send",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}, "description": "The accepted reply stream."}},
)
def send_queued(
    payload: QueueSend,
    conversation: ConversationDependency,
    session: SessionDependency,
    service: ReplyServiceDependency,
) -> StreamingResponse:
    """Send selected queue rows with the user message that consumes them."""
    try:
        stream = service.send_queued(session, conversation, payload.item_id, payload.all)
    except ReplyError as error:
        raise reply_http_error(error) from error
    return event_stream(stream.events)


@router.post("/conversations/{conversation_id}/queue/take", response_model=QueueTaken)
def take_from_queue(payload: QueueTake, conversation: ConversationDependency, session: SessionDependency) -> QueueTaken:
    """Old clients must use queue/send so a failed send cannot consume their messages."""
    del payload, session
    raise HTTPException(
        status.HTTP_409_CONFLICT,
        "Queue items are no longer removed before sending. Refresh YAAP and send the queued message again.",
    )


class SteerIn(BaseModel):
    # The text to send, or a queued message to send instead of waiting.
    text: str = Field(default="", max_length=20_000)
    item_id: int | None = None


class SteerOut(BaseModel):
    status: Literal["waiting"]


@router.post("/conversations/{conversation_id}/steer", response_model=SteerOut, status_code=status.HTTP_202_ACCEPTED)
def steer(payload: SteerIn, conversation: ConversationDependency, session: SessionDependency, request: Request) -> SteerOut:
    """Send a message to the running reply: the agent gets it with the result of its next tool call.
    When it makes no further tool call, the message is queued and sent when the reply ends."""
    registry: RunRegistry = request.app.state.runs
    run = registry.for_conversation(conversation.id) if request.app.state.replies.running(conversation.id) else None
    if run is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "No reply with tools is running; queue the message instead.")
    text = payload.text.strip()
    if payload.item_id is None and not text:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A steering message needs text.")
    if run.state.record_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This reply cannot accept steering right now; queue the message instead.")
    try:
        add_steering(session, conversation.id, run.state.record_id, text, payload.item_id)
    except QueueError as error:
        code = status.HTTP_404_NOT_FOUND if str(error) == "Queued message not found." else status.HTTP_422_UNPROCESSABLE_CONTENT
        raise HTTPException(code, str(error)) from error
    return SteerOut(status="waiting")


class WaitingApprovalOut(BaseModel):
    approval_id: str
    conversation_id: int
    tool: str
    summary: str


@router.get("/environments/{environment_id}/approvals", response_model=list[WaitingApprovalOut])
def waiting_approvals(environment: EnvironmentDependency, request: Request) -> list[WaitingApprovalOut]:
    """Approvals that runs in this environment wait for, across conversations."""
    registry: RunRegistry = request.app.state.runs
    return [
        WaitingApprovalOut(
            approval_id=approval.id, conversation_id=run.conversation_id, tool=approval.tool, summary=approval.summary
        )
        for run in registry.runs.values()
        if run.environment_id == environment.id
        for approval in run.state.approvals.values()
        if not approval.future.done()
    ]
