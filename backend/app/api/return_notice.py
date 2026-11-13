"""The notice after a pause and the continuation from a summary.

Every route is under its conversation and reads it through the conversation APIs' own dependency.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from app.api.conversation_mapping import summary_out
from app.api.conversations import ConversationDependency, utility_context
from app.api.dependencies import ProviderRegistryDependency, SessionDependency
from app.api.schemas import ConversationSummaryOut
from app.core.conversation_continuation import LinkedConversation, continuation_links, continue_from_summary
from app.core.return_notice import Billing, conversation_return_notice
from app.core.summaries import SummaryError
from app.storage.models import Conversation
from app.providers.base import CacheTtl

router = APIRouter(tags=["conversations"])


class ReturnNoticeBodyOut(BaseModel):
    idle_since: datetime
    cache_lifetime_minutes: int
    context_tokens: int
    billing: Billing
    cache_ttl: CacheTtl
    # Estimated dollars to write the context to the cache again; None on a subscription or without a price.
    cost: float | None
    can_summarise: bool


class ReturnNoticeOut(BaseModel):
    notice: ReturnNoticeBodyOut | None


class LinkedConversationOut(BaseModel):
    id: int
    title: str


class ContinuationOut(BaseModel):
    continued_from: LinkedConversationOut | None
    continued_in: list[LinkedConversationOut]


def linked_out(link: LinkedConversation) -> LinkedConversationOut:
    return LinkedConversationOut(id=link.id, title=link.title)


@router.get("/conversations/{conversation_id}/return-notice", response_model=ReturnNoticeOut)
def get_return_notice(
    request: Request,
    conversation: ConversationDependency,
    session: SessionDependency,
    providers: ProviderRegistryDependency,
) -> ReturnNoticeOut:
    """What continuing costs after a pause long enough for the prompt cache to expire; none while a reply runs."""
    if request.app.state.replies.busy(conversation.id):
        return ReturnNoticeOut(notice=None)
    notice = conversation_return_notice(session, providers, conversation, datetime.now(UTC))
    if notice is None:
        return ReturnNoticeOut(notice=None)
    return ReturnNoticeOut(
        notice=ReturnNoticeBodyOut(
            idle_since=notice.idle_since,
            cache_lifetime_minutes=int(notice.cache_lifetime.total_seconds() // 60),
            context_tokens=notice.context_tokens,
            billing=notice.billing,
            cache_ttl=notice.cache_ttl,
            cost=notice.cost,
            can_summarise=notice.can_summarise,
        )
    )


@router.get("/conversations/{conversation_id}/continuation", response_model=ContinuationOut)
def get_continuation(conversation: ConversationDependency, session: SessionDependency) -> ContinuationOut:
    """The conversation this one continues, and the ones that continue it."""
    links = continuation_links(session, conversation)
    return ContinuationOut(
        continued_from=linked_out(links.continued_from) if links.continued_from else None,
        continued_in=[linked_out(link) for link in links.continued_in],
    )


@router.post("/conversations/{conversation_id}/continue-from-summary", response_model=ConversationSummaryOut)
async def continue_conversation_from_summary(
    request: Request, conversation: ConversationDependency, session: SessionDependency
) -> ConversationSummaryOut:
    """Start a new conversation from a summary of this one, with the same settings; returns the new one."""
    if request.app.state.replies.busy(conversation.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Wait until YAAP has finished replying.")
    conversation_id = conversation.id
    session.close()
    try:
        result = await continue_from_summary(utility_context(request), conversation_id)
    except SummaryError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    return summary_out(session.get_one(Conversation, result.conversation_id))
