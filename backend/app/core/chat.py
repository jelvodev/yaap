"""Plain chat: one user message in, one streamed assistant reply out, stored as an append-only tree."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from types import EllipsisType
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agent.runs import AgentRun, RunRegistry
from app.agents.records import finish_record, finish_run_record
from app.attachments.service import AttachmentResolver, attachment_block
from app.core.conversation_schemas import ConversationSummaryOut, message_to_out
from app.core.message_parts import model_content
from app.core.model_choice import Billing
from app.core.prompts import base_system_parts
from app.core.stopped_usage import StoppedRun, store_stopped_usage
from app.core.summaries import apply_summary
from app.core.trimming import apply_trim
from app.imports.sessions import imported_model_content, is_imported
from app.providers.base import (
    AgentEvent,
    AgentRequest,
    CallRestarted,
    Completed,
    HistoryMessage,
    ModelProvider,
    ProviderError,
    ReasoningDelta,
    SystemPart,
    TextDelta,
    ToolCallFinished,
    ToolCallStarted,
)
from app.providers.catalog import calculate_cost
from app.providers.registry import ProviderRegistry
from app.storage.models import Attachment, Conversation, Message, UsageRecord

logger = logging.getLogger(__name__)

DEFAULT_TITLE = "New conversation"
TITLE_LENGTH = 60


def load_branch(session: Session, conversation_id: int, head_message_id: int | None) -> list[Message]:
    """Messages from the root to the given head, following parent links."""
    if head_message_id is None:
        return []
    messages = {
        message.id: message
        for message in session.scalars(select(Message).where(Message.conversation_id == conversation_id))
    }
    branch: list[Message] = []
    current = messages.get(head_message_id)
    while current is not None:
        branch.append(current)
        current = messages.get(current.parent_id) if current.parent_id is not None else None
    branch.reverse()
    return branch


def is_part_of_history(message: Message) -> bool:
    """Only turns the model actually completed are sent back; cancelled, failed and refused replies are not."""
    if message.role == "user":
        return True
    return message.provider_raw is not None and message.status != "refused"


def build_history(branch: list[Message], resolver: AttachmentResolver | None = None) -> list[HistoryMessage]:
    """The branch as model input: commands filled in, skills as text, attachments as images, documents or text."""
    history = []
    for message in branch:
        if not is_part_of_history(message):
            continue
        content = message.content
        if message.role == "user":
            content = model_content(content)
            if resolver is not None:
                content = resolver.expand(content)
        elif is_imported(message.provider_raw):
            # A turn of an imported Claude Code or Codex session: its tool calls go along as text.
            content = imported_model_content(content, message.provider_raw[0].get("tool", ""))  # type: ignore[index]
        history.append(HistoryMessage(role=message.role, content=content, provider_raw=message.provider_raw))  # type: ignore[arg-type]
    return history


def make_title(text: str) -> str:
    first_line = " ".join(text.split())
    return first_line if len(first_line) <= TITLE_LENGTH else first_line[: TITLE_LENGTH - 1].rstrip() + "…"


def add_user_message(
    session: Session,
    conversation: Conversation,
    text: str,
    attachments: Sequence[Attachment] = (),
    extra_blocks: Sequence[dict[str, Any]] = (),
    parent_id: int | None | EllipsisType = ...,
) -> Message:
    """Store the user's message: skills first, then the typed text, its command expansion and attachments.

    It follows the current head, unless parent_id says where it goes (a new version of an earlier message).
    """
    now = datetime.now(UTC)
    skills = [block for block in extra_blocks if block["type"] in ("skill", "skill_removed")]
    commands = [block for block in extra_blocks if block["type"] in ("command", "goal")]
    content: list[dict[str, Any]] = [*skills]
    content += [{"type": "text", "text": text}] if text else []
    content += commands
    content += [attachment_block(attachment) for attachment in attachments]
    message = Message(
        conversation_id=conversation.id,
        parent_id=conversation.head_message_id if parent_id is ... else parent_id,
        role="user",
        content=content,
        status="complete",
        created_at=now,
    )
    session.add(message)
    session.flush()
    for attachment in attachments:
        # A new version of a message shares its files; they stay with the message they came with.
        if attachment.message_id is None:
            attachment.message_id = message.id
    conversation.head_message_id = message.id
    conversation.updated_at = now
    if conversation.title == DEFAULT_TITLE:
        conversation.title = make_title(text or ", ".join(attachment.filename for attachment in attachments))
    session.commit()
    return message


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


def server_sent_event(name: str, payload: dict[str, Any]) -> str:
    return f"event: {name}\ndata: {json.dumps(payload)}\n\n"


@dataclass
class ReplyOutcome:
    status: str
    stop_reason: str | None
    error: str | None


def outcome_from_completion(completed: Completed) -> ReplyOutcome:
    if completed.stop_reason == "refusal":
        category = f" (category: {completed.refusal_category})" if completed.refusal_category else ""
        return ReplyOutcome("refused", completed.stop_reason, f"The model declined this request{category}.")
    if completed.stop_reason == "max_tokens":
        return ReplyOutcome("incomplete", completed.stop_reason, "The reply reached the maximum length.")
    if completed.stop_reason == "max_turns":
        return ReplyOutcome("incomplete", completed.stop_reason, "The reply reached the limit of model calls in one run.")
    return ReplyOutcome("complete", completed.stop_reason, None)


MAX_STORED_TOOL_OUTPUT = 20_000


class ReplyContent:
    """The reply in the order it happened: reasoning, text and tool calls, as neutral content blocks."""

    def __init__(self) -> None:
        self.blocks: list[dict[str, Any]] = []

    def add_text(self, kind: str, text: str) -> None:
        if self.blocks and self.blocks[-1]["type"] == kind:
            self.blocks[-1]["text"] += text
        else:
            self.blocks.append({"type": kind, "text": text})

    def start_tool(self, event: ToolCallStarted) -> None:
        self.blocks.append(
            {"type": "tool_call", "id": event.id, "name": event.name, "input": event.input, "output": None, "is_error": False}
        )

    def drop_unfinished_call(self) -> None:
        """Remove text and reasoning after the last tool call: a model call that broke off and starts again."""
        while self.blocks and self.blocks[-1]["type"] in ("text", "reasoning"):
            self.blocks.pop()

    def finish_tool(self, event: ToolCallFinished) -> None:
        for block in reversed(self.blocks):
            if block["type"] == "tool_call" and block["id"] == event.id:
                output = event.output
                if len(output) > MAX_STORED_TOOL_OUTPUT:
                    output = output[:MAX_STORED_TOOL_OUTPUT] + "\n[Output shortened for storage.]"
                block.update(output=output, is_error=event.is_error)
                return

    def content(self) -> list[dict[str, Any]]:
        blocks = [
            {**block, "text": block["text"].strip()} if block["type"] == "reasoning" else block
            for block in self.blocks
            if block["type"] != "reasoning" or block["text"].strip()
        ]
        if not any(block["type"] == "text" for block in blocks):
            blocks.append({"type": "text", "text": ""})
        return blocks


async def merge_events(
    provider_events: AsyncIterator[AgentEvent], run_events: "asyncio.Queue[dict[str, Any]] | None"
) -> AsyncIterator[AgentEvent | dict[str, Any]]:
    """The model's events, interleaved with the run's own events (approval requests) as they happen."""
    if run_events is None:
        async for event in provider_events:
            yield event
        return
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

    async def pump_provider() -> None:
        try:
            async for event in provider_events:
                await queue.put(("event", event))
            await queue.put(("done", None))
        except Exception as error:  # noqa: BLE001 - handed to the consumer, which re-raises it
            await queue.put(("error", error))

    async def pump_run() -> None:
        while True:
            await queue.put(("event", await run_events.get()))

    tasks = [asyncio.create_task(pump_provider()), asyncio.create_task(pump_run())]
    try:
        while True:
            kind, value = await queue.get()
            if kind == "done":
                # Run events that came with the provider's last steps (a steering marker, live output) still go out.
                while not queue.empty():
                    kind, value = queue.get_nowait()
                    if kind == "event":
                        yield value
                while not run_events.empty():
                    yield run_events.get_nowait()
                return
            if kind == "error":
                raise value
            yield value
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


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
    try:
        try:
            async for event in merge_events(provider.stream(request), run.events if run else None):
                if isinstance(event, TextDelta):
                    reply.add_text("text", event.text)
                    yield server_sent_event("text_delta", {"text": event.text})
                elif isinstance(event, ReasoningDelta):
                    reply.add_text("reasoning", event.text)
                    yield server_sent_event("reasoning_delta", {"text": event.text})
                elif isinstance(event, ToolCallStarted):
                    reply.start_tool(event)
                    yield server_sent_event("tool_call", {"id": event.id, "name": event.name, "input": event.input})
                elif isinstance(event, ToolCallFinished):
                    reply.finish_tool(event)
                    yield server_sent_event(
                        "tool_result", {"id": event.id, "output": event.output[:MAX_STORED_TOOL_OUTPUT], "is_error": event.is_error}
                    )
                elif isinstance(event, CallRestarted):
                    reply.drop_unfinished_call()
                    yield server_sent_event("call_restarted", {"attempt": event.attempt, "reason": event.reason})
                elif isinstance(event, Completed):
                    completed = event
                elif isinstance(event, dict):
                    # The run's own events: approval requests and decisions, live output, steering messages.
                    name = event.pop("event")
                    if name == "steering":
                        reply.blocks.append({"type": "steering", "text": event["text"]})
                    yield server_sent_event(name, event)
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
            store_unfinished_usage(
                session_factory, conversation, message.id, run, record_id, request.model or conversation.model, billing
            )
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
    finally:
        if not stored:
            # The client went away mid-stream: keep what arrived, marked incomplete and left out of history.
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
            store_unfinished_usage(
                session_factory, conversation, stopped.id, run, record_id, request.model or conversation.model, billing
            )
        if run is not None and runs is not None:
            runs.finish(run)
            tool_calls = sum(1 for block in reply.blocks if block["type"] == "tool_call")
            finish_run_record(session_factory, run, reply_id, final_status, tool_calls)
        elif record_id is not None:
            finish_record(session_factory, record_id, reply_id, final_status, 0)
        try:
            await provider.close()
        except Exception:  # noqa: BLE001 - closing must never hide the original outcome
            logger.warning("Closing the provider client failed.", exc_info=True)
