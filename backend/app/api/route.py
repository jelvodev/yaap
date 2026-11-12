"""The route of an answer (tracing): the stops a reply passed, with the checks and the cost of each."""

from fastapi import APIRouter, HTTPException, status

from app.api.conversations import ConversationDependency
from app.api.dependencies import SessionDependency
from app.storage.models import Message
from app.tracing.route_load import reply_route
from app.tracing.route_schema import RouteOut

router = APIRouter(tags=["tracing"])


@router.get("/conversations/{conversation_id}/messages/{message_id}/route", response_model=RouteOut)
def read_route(conversation: ConversationDependency, message_id: int, session: SessionDependency) -> RouteOut:
    """The route of a reply: message, route, context, model calls, tools, helpers, answer and what came after."""
    message = session.get(Message, message_id)
    if message is None or message.conversation_id != conversation.id or message.role != "assistant":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Reply not found in this conversation.")
    return reply_route(session, conversation.id, message_id) or RouteOut(stops=[])
