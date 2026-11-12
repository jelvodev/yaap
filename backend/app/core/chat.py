"""Plain chat: one user message in, one streamed assistant reply out, stored as an append-only tree."""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import anyio
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.agent.runs import AgentRun, RunRegistry
from app.agents.records import finish_record, finish_run_record
from app.attachments.service import AttachmentResolver
from app.core.chat_events import (
    ReplyContent,
    ReplyOutcome,
    merge_events,
    outcome_from_completion,
    reply_event,
    server_sent_event,
)
from app.core.chat_history import build_history
from app.core.conversation_schemas import ConversationSummaryOut, message_to_out
from app.core.model_choice import Billing
from app.core.prompts import base_system_parts
from app.core.stopped_usage import StoppedRun, store_stopped_usage
from app.core.summaries import apply_summary
from app.core.trimming import apply_trim
from app.core.utility_skips import skipped_since
from app.providers.base import AgentRequest, Completed, ModelProvider, ProviderError, SystemPart
from app.providers.catalog import calculate_cost
from app.providers.registry import ProviderRegistry
from app.storage.models import Conversation, Message, UsageRecord


logger = logging.getLogger(__name__)


def build_request(
    conversation: Conversation,
    branch: list[Message],
    system: list[SystemPart] | None = None,
    resolver: AttachmentResolver | None = None,
    max_tokens: int | None = None,
) -> AgentRequest:
    """The request for the next reply. max_tokens is the reply length; None keeps the default."""
    return AgentRequest(
        model=conversation.model,
        effort=conversation.effort,
        **({"max_tokens": max_tokens} if max_tokens else {}),
        system=system if system is not None else base_system_parts(),
        # From a summary on, the model gets the summary instead of the messages it covers.
        # Old tool output shortened, then the summary.
        messages=apply_summary(branch, apply_trim(branch, build_history(branch, resolver))),
    )


def finish_reply(
    session_factory: sessionmaker[Session],
    providers: ProviderRegistry,
    conversation_id: int,
    user_message: Message,
    request: AgentRequest,
    reply: ReplyContent,
    stored: bool,
    reply_id: int | None,
    outcome: ReplyOutcome,
    run: AgentRun | None,
    record_id: int | None,
    route: dict[str, Any] | None,
    billing: Billing,
) -> tuple[int | None, str]:
    """Store an unfinished reply when needed, then finalize its usage and run record independently."""
    final_status = outcome.status if stored else "incomplete"
    conversation: Conversation | None = None
    if not stored:
        try:
            stopped, conversation = store_assistant_reply(
                session_factory,
                providers,
                conversation_id,
                user_message.id,
                reply.content(),
                ReplyOutcome("incomplete", None, "The reply was stopped before it finished."),
                None,
                route,
            )
            reply_id = stopped.id
            final_status = "incomplete"
        except SQLAlchemyError:
            logger.warning("Storing the unfinished reply failed.", exc_info=True)
    if reply_id is not None and conversation is not None:
        try:
            store_unfinished_usage(
                session_factory, conversation, reply_id, run, record_id, request.model or conversation.model, billing
            )
        except SQLAlchemyError:
            logger.warning("Storing unfinished reply usage failed.", exc_info=True)

    tool_calls = sum(1 for block in reply.blocks if block["type"] == "tool_call")
    try:
        if run is not None:
            finish_run_record(session_factory, run, reply_id, final_status, tool_calls)
        elif record_id is not None:
            finish_record(session_factory, record_id, reply_id, final_status, 0)
    except SQLAlchemyError:
        logger.warning("Storing the reply run record failed.", exc_info=True)
    return reply_id, final_status




def store_assistant_reply(
    session_factory: sessionmaker[Session],
    providers: ProviderRegistry,
    conversation_id: int,
    parent_id: int,
    content: list[dict[str, Any]],
    outcome: ReplyOutcome,
    completed: Completed | None,
    route: dict[str, Any] | None = None,
) -> tuple[Message, Conversation]:
    with session_factory() as session:
        now = datetime.now(UTC)
        conversation = session.get_one(Conversation, conversation_id)
        message = Message(
            conversation_id=conversation_id,
            parent_id=parent_id,
            role="assistant",
            content=content,
            provider_raw=completed.provider_raw if completed else None,
            status=outcome.status,
            stop_reason=outcome.stop_reason,
            error=outcome.error,
            created_at=now,
            route=route,
        )
        session.add(message)
        session.flush()
        conversation.head_message_id = message.id
        conversation.updated_at = now
        if completed is not None:
            served_model = providers.find_model(completed.served_by_model)
            metered = completed.billing == "metered"
            session.add(
                UsageRecord(
                    environment_id=conversation.environment_id,
                    conversation_id=conversation_id,
                    message_id=message.id,
                    provider=conversation.provider,
                    model=completed.served_by_model,
                    purpose="main",
                    input_tokens=completed.usage.input_tokens,
                    output_tokens=completed.usage.output_tokens,
                    cache_read_tokens=completed.usage.cache_read_tokens,
                    cache_write_tokens=completed.usage.cache_write_tokens,
                    # Unknown model (for example a fallback model outside the catalog) or a subscription: no cost.
                    cost=calculate_cost(served_model, completed.usage) if served_model and metered else None,
                    billing=completed.billing,
                    context_tokens=completed.context_tokens,
                    created_at=now,
                )
            )
        session.commit()
        return message, conversation


def store_unfinished_usage(
    session_factory: sessionmaker[Session],
    conversation: Conversation,
    message_id: int,
    run: AgentRun | None,
    record_id: int | None,
    model: str,
    billing: Billing,
) -> None:
    """Store the usage of a reply that got no final message from the provider: stopped, failed on a provider error,
    or ended without one. Its usage is what its recorded model calls add up to, linked to the stored message.

    The calls are recorded under the agent run's record, or under record_id for a reply without an agent run;
    without either there are no recorded calls and nothing is stored.
    """
    steps_run_id = run.state.record_id if run is not None else record_id
    if steps_run_id is None:
        return
    stopped_run = StoppedRun(
        steps_run_id,
        conversation.environment_id,
        conversation.id,
        message_id,
        conversation.provider,
        model,
        "main",
        billing,
    )
    store_stopped_usage(session_factory, stopped_run)


def conversation_summary(conversation: Conversation) -> dict[str, Any]:
    return ConversationSummaryOut(
        id=conversation.id,
        project_id=conversation.project_id,
        title=conversation.title,
        provider=conversation.provider,
        model=conversation.model,
        effort=conversation.effort,
        agent=conversation.agent,
        pinned_skills=conversation.pinned_skills or [],
        updated_at=conversation.updated_at,
        scheduled=conversation.scheduled_task_id is not None,
        unread=bool(conversation.unread),
        imported=conversation.imported_from or None,
    ).model_dump(mode="json")


async def apply_title(
    session_factory: sessionmaker[Session],
    conversation_id: int,
    title_task: Callable[[str], Awaitable[str | None]],
    reply_text: str,
) -> dict[str, Any] | None:
    try:
        title = await title_task(reply_text)
    except Exception:  # noqa: BLE001 - a title is a nicety; the reply is already stored
        logger.warning("Proposing a conversation title failed.", exc_info=True)
        return None
    if not title:
        return None
    with session_factory() as session:
        conversation = session.get_one(Conversation, conversation_id)
        conversation.title = title
        session.commit()
        return conversation_summary(conversation)


async def stream_reply(
    session_factory: sessionmaker[Session],
    providers: ProviderRegistry,
    provider: ModelProvider,
    conversation_id: int,
    user_message: Message,
    request: AgentRequest,
    run: AgentRun | None = None,
    runs: RunRegistry | None = None,
    title_task: Callable[[str], Awaitable[str | None]] | None = None,
    record_id: int | None = None,
    route: dict[str, Any] | None = None,
    billing: Billing = "metered",
) -> AsyncIterator[str]:
    """Stream the reply as server-sent events. The reply is stored even when the client disconnects.

    record_id is the run record of a reply without an agent run; it is finished with the reply. route is how the
    preset routed the reply, stored with it. A reply that is stopped, fails on a provider error or ends without a final
    message gets no usage from the provider: its usage is what its recorded model calls add up to, billed as billing
    says.

    After the first reply of a conversation, title_task proposes a better title; it arrives as a "title" event
    after "done", so the user is not kept waiting for it.
    """
    yield server_sent_event("user_message", message_to_out(user_message).model_dump(mode="json"))
    reply = ReplyContent()
    completed: Completed | None = None
    stored = False
    final_status = "incomplete"
    reply_id: int | None = None
    outcome = ReplyOutcome("incomplete", None, "The reply was stopped before it finished.")
    try:
        try:
            async for event in merge_events(provider.stream(request), run.events if run else None):
                if isinstance(event, Completed):
                    completed = event
                    continue
                converted = reply_event(event, reply)
                if converted is not None:
                    yield server_sent_event(*converted)
            outcome = (
                outcome_from_completion(completed)
                if completed
                else ReplyOutcome("error", None, "The provider ended the stream without a final message.")
            )
        except ProviderError as error:
            completed = None
            outcome = ReplyOutcome("error", None, error.message)

        message, conversation = store_assistant_reply(
            session_factory,
            providers,
            conversation_id,
            user_message.id,
            reply.content(),
            outcome,
            completed,
            route,
        )
        stored = True
        final_status, reply_id = outcome.status, message.id
        if completed is None:
            # A provider error or a missing final message: the calls that returned before it still cost.
            try:
                store_unfinished_usage(
                    session_factory,
                    conversation,
                    message.id,
                    run,
                    record_id,
                    request.model or conversation.model,
                    billing,
                )
            except SQLAlchemyError:
                logger.warning("Storing unfinished reply usage failed.", exc_info=True)
        yield server_sent_event(
            "done",
            {
                "message": message_to_out(message).model_dump(mode="json"),
                "conversation": conversation_summary(conversation),
            },
        )
        if title_task is not None and outcome.status == "complete":
            reply_text = "".join(block["text"] for block in reply.content() if block["type"] == "text")
            summary = await apply_title(session_factory, conversation_id, title_task, reply_text)
            if summary is not None:
                yield server_sent_event("title", {"conversation": summary})
        # Utility tasks of this exchange that the model policy skipped (routing, outside-content checks, the title).
        for skip in skipped_since(session_factory, conversation_id, user_message.created_at):
            yield server_sent_event("utility_skipped", skip.model_dump(mode="json"))
    finally:
        # YAAP runs replies in the background; only a stop or failure leaves this path incomplete.
        try:
            # Shielded so cancellation cannot interrupt provider cleanup.
            with anyio.CancelScope(shield=True):
                await provider.close()
        except Exception:  # noqa: BLE001 - closing must never hide the original outcome
            logger.warning("Closing the provider client failed.", exc_info=True)
        finally:
            try:
                if run is not None and runs is not None:
                    runs.finish(run)
            finally:
                finish_reply(
                    session_factory,
                    providers,
                    conversation_id,
                    user_message,
                    request,
                    reply,
                    stored,
                    reply_id,
                    outcome,
                    run,
                    record_id,
                    route,
                    billing,
                )
