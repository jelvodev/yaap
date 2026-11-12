"""Starting a reply: the one service behind every entry point that makes YAAP reply.

A message the user sends, a regenerated reply, the next turn of a goal, a scheduled task and an A2A task all go
through `ReplyService`: `prepare` checks that the reply can start and gathers what it needs before anything is
stored, `launch` starts it in the background. A reply that cannot start raises a `ReplyError`; the HTTP layer turns
it into a status code, the other entry points report its message where their run is shown.

The service runs in a worker thread (a sync route, the scheduler's thread, A2A's thread), as the reply registry and
the browser are reached on the event loop from there.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, replace
from functools import partial

import anyio
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.agent.runs import AgentRun
from app.agent.tool_catalog import with_listing
from app.agent.toolbox import RunToolBox
from app.agents.records import finish_record, finish_run_record
from app.attachments.files import AttachmentError
from app.attachments.service import AttachmentResolver, claim_attachments, conversation_attachments
from app.blueprint.check import is_read_only_check
from app.core.audit import record_request, reply_entry, with_refusal
from app.core.cache_policy import reply_cache_ttl
from app.core.budget_stop import both_listeners, reply_budget_guard, with_budget_stop
from app.core.chat import build_request, stream_reply
from app.core.chat_history import add_user_message, load_branch
from app.core.context_control import output_tokens
from app.core.goals import end_goal, goal_block, next_turn
from app.core.message_parts import expand_command
from app.core.model_choice import Billing
from app.core.message_queue import QueueError, select_for_send, select_ids_for_send
from app.core.outgoing_checks import checked_provider, request_refusal
from app.core.queue_commands import EVAL_COMMAND
from app.core.replies import ReplyJob, Reservation
from app.core.reply_commands import QueuedCommand, ReplyCommands, command_blocks
from app.core.reply_errors import InvalidReplyRequest, ReplyBusy, ReplyError
from app.core.reply_events import (
    BACKGROUND_WORK,
    TitleRequest,
    first_title,
    with_goal_follow_up,
    with_index_nudge,
    with_memory_suggestions,
    with_steering_fallback,
    with_summary_check,
)
from app.core.reply_blueprint_versions import record_blueprint_versions
from app.core.reply_repo_map import with_repo_map
from app.core.reply_routing import route_reply
from app.core.reply_run import reply_model_trace, run_folder, start_agent_run
from app.core.reply_services import ReplyServices
from app.core.reply_setup import ReplySetup, prepare_reply
from app.core.search_index import message_text
from app.core.summaries import compact_focus
from app.core.turn_context import with_turn_context
from app.learning.after_reply import with_learning_review
from app.providers.base import AgentRequest, HistoryMessage, ToolServer
from app.reviews.service import ReviewService
from app.storage.models import Attachment, Conversation, Goal, Message, QueuedMessage
from app.tracing.otlp_export import with_telemetry_export
from app.tracing.provider import traced_provider
from app.tracing.recorder import StepListener

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class ReplyRequest:
    """A reply to start: to which message, and how this one reply differs from the conversation's settings."""

    conversation: Conversation
    user_message: Message
    # The text a title is proposed from, on the first exchange of a conversation.
    title_text: str | None = None
    # This one reply is read only (/check), whatever access the conversation has.
    read_only: bool = False
    # Nobody approves (a scheduled task); a conversation with a running scheduled task is unattended anyway.
    unattended: bool = False
    # In an unattended reply: tools that run without asking, by name (an eval case's allowed_tools).
    tools_by_name: tuple[str, ...] = ()
    # Hears every step of the reply as it is recorded (an eval case watching its budget).
    on_step: StepListener | None = None
    # The messages the model gets instead of the conversation's branch (a replay starts with what an earlier model
    # call saw); they are sent as given, turn context included.
    history: tuple[HistoryMessage, ...] | None = None


@dataclass(frozen=True)
class NewMessage:
    """A message the user sends: after the current head, or with edit_of as a new version of an earlier message."""

    text: str
    edit_of: int | None = None
    attachment_ids: tuple[int, ...] = ()
    queued_item_ids: tuple[int, ...] = ()
    # The send-now job that holds the queued rows, when they are sent by one.
    handoff_job_id: str | None = None


@dataclass(frozen=True)
class ReplyStream:
    """What a sent message streams back: the reply's events, or for /compact the summary's events."""

    events: AsyncIterator[str]
    # The running reply; None for /compact.
    job: ReplyJob | None = None


class ReplyService:
    def __init__(self, services: ReplyServices) -> None:
        self.services = services
        self.commands = ReplyCommands(services)

    def prepare(self, session: Session, conversation: Conversation, query: str = "") -> ReplySetup:
        return prepare_reply(self.services, session, conversation, query)

    def reserve(self, conversation_id: int) -> Reservation:
        return self.services.replies.reserve(conversation_id)

    def launch(
        self, session: Session, setup: ReplySetup, request: ReplyRequest, reservation: Reservation
    ) -> ReplyJob:
        """Start the reply to request.user_message in the background; the job can be followed from its start."""
        services, conversation = self.services, request.conversation
        if reservation.conversation_id != conversation.id:
            raise ReplyBusy("The reply reservation belongs to a different conversation.")
        if request.history is None:
            # A replay keeps the model it was given; every other reply routes when its preset does.
            setup = replace(setup, route=route_reply(services, session, conversation, request.user_message))
        model, effort = setup.reply_model(conversation)
        attachments = conversation_attachments(session, conversation.id)
        runs_tools = setup.provider_spec.runs_agents
        resolver = AttachmentResolver(session, services.files_dir, with_tools=runs_tools)
        branch = load_branch(session, conversation.id, request.user_message.id)
        max_tokens = output_tokens(session, services.providers, conversation.environment_id, conversation.preset, model)
        agent_request = build_request(conversation, branch, setup.system, resolver, max_tokens)
        if request.history is not None:
            messages = list(request.history)
        else:
            messages = with_turn_context(agent_request.messages, setup.turn_context)
        agent_request = replace(
            agent_request,
            model=model,
            effort=effort,
            messages=messages,
            cache_ttl=reply_cache_ttl(session, conversation, branch, setup.provider_spec),
        )
        conversation_id = conversation.id
        guard = reply_budget_guard(
            session, lambda: services.replies.cancel(conversation_id), conversation.environment_id
        )
        on_step = both_listeners(request.on_step, guard)
        run = None
        if runs_tools:
            run, agent_request = self.start_run(session, setup, request, attachments, agent_request, on_step)
        provider = None
        trace = None
        try:
            entry = reply_entry(conversation, setup.provider_spec, agent_request, setup.project_data, bool(attachments))
            record_request(services.session_factory, with_refusal(entry, request_refusal(agent_request, setup.outgoing)))
            trace = reply_model_trace(services, session, conversation, setup, run, on_step)
            billing: Billing = "metered" if setup.provider_spec.metered else "subscription"
            provider = traced_provider(checked_provider(setup.provider_spec.create(setup.api_key), setup.outgoing), trace)
            events = stream_reply(
                services.session_factory, services.providers, provider, conversation.id, request.user_message,
                agent_request, run, services.runs, self.title_task(conversation, setup, request.title_text),
                record_id=trace.run_id if run is None else None,
                route=setup.route.stored() if setup.route is not None else None, billing=billing,
            )
            if guard is not None:
                events = with_budget_stop(services.session_factory, guard, conversation_id, events)
            events = self.after_reply(conversation, run, events)
            events = with_telemetry_export(services.telemetry, trace.run_id, events)
            return anyio.from_thread.run_sync(services.replies.start, reservation, conversation.environment_id, events)
        except BaseException:  # A failed launch never reaches the stream's normal cleanup.
            if run is not None:
                services.runs.finish(run)
                finish_run_record(services.session_factory, run, None, "incomplete", 0)
            elif trace is not None:
                finish_record(services.session_factory, trace.run_id, None, "incomplete", 0)
            if provider is not None:
                try:
                    anyio.from_thread.run(provider.close)
                except Exception:  # A cleanup failure must not hide the original start failure.
                    logger.warning("Closing the provider after a failed reply start failed.", exc_info=True)
            raise

    def start_run(
        self,
        session: Session,
        setup: ReplySetup,
        request: ReplyRequest,
        attachments: list[Attachment],
        agent_request: AgentRequest,
        on_step: StepListener | None,
    ) -> tuple[AgentRun, AgentRequest]:
        """The reply's agent run, and the request with the run's tools in it."""
        services = self.services
        run = start_agent_run(
            services,
            session,
            request.conversation,
            setup,
            attachments,
            request.read_only,
            request.unattended,
            request.tools_by_name,
            on_step,
        )
        record_blueprint_versions(session, setup, run)
        try:
            return run, replace(
                agent_request,
                # The run's outside MCP servers and repository map are known once its tools are loaded.
                system=with_repo_map(services, session, setup, run, with_listing(agent_request.system, run.catalog)),
                working_folder=run_folder(services, setup),
                tool_server=ToolServer(url=f"{services.internal_url}/mcp", token=run.token),
                tool_box=RunToolBox(services.runs, run),
            )
        except BaseException:  # Failure before launch must not leave a usable tool-server token.
            services.runs.finish(run)
            finish_run_record(services.session_factory, run, None, "incomplete", 0)
            raise

    def title_task(
        self, conversation: Conversation, setup: ReplySetup, title_text: str | None
    ) -> Callable[[str], Awaitable[str | None]] | None:
        if title_text is None or not self.services.utility_titles:
            return None
        services = self.services
        title = TitleRequest(conversation.environment_id, conversation.id, title_text, setup.languages.reply)

        async def propose(reply_text: str) -> str | None:
            return await first_title(services, title, reply_text)

        return propose

    def after_reply(
        self, conversation: Conversation, run: AgentRun | None, events: AsyncIterator[str]
    ) -> AsyncIterator[str]:
        services, conversation_id = self.services, conversation.id
        if run is not None:
            events = with_steering_fallback(services, conversation_id, run, events)
        if conversation.project_id is not None:
            events = with_memory_suggestions(services, conversation_id, events)
            events = with_learning_review(services, conversation_id, events)
            events = with_goal_follow_up(services, conversation_id, events, self.send_goal_turn_later)
        events = with_summary_check(services, conversation_id, events)
        return with_index_nudge(services, events)

    def send(self, session: Session, conversation: Conversation, message: NewMessage) -> ReplyStream:
        """Send the user's message and start the reply, or summarise now for /compact."""
        with self.services.replies.reserve(conversation.id) as reservation:
            current = session.get(Conversation, conversation.id, populate_existing=True)
            if current is None:
                raise InvalidReplyRequest("This conversation no longer exists.")
            return self._send_reserved(session, current, message, reservation)

    def send_queued(
        self, session: Session, conversation: Conversation, item_id: int | None, send_all: bool
    ) -> ReplyStream:
        """Accept queued rows together with the user message that consumes them."""
        return self._send_selection(
            session, conversation, lambda current: select_for_send(session, current.id, item_id, send_all)
        )

    def send_queued_ids(
        self, session: Session, conversation: Conversation, item_ids: list[int], job_id: str
    ) -> ReplyStream:
        """Send the exact queue rows saved in a durable send-now handoff."""
        return self._send_selection(
            session, conversation, lambda current: select_ids_for_send(session, current.id, item_ids, job_id), job_id
        )

    def _send_selection(
        self,
        session: Session,
        conversation: Conversation,
        choose: Callable[[Conversation], list[QueuedMessage]],
        handoff_job_id: str | None = None,
    ) -> ReplyStream:
        with self.services.replies.reserve(conversation.id) as reservation:
            current = session.get(Conversation, conversation.id, populate_existing=True)
            if current is None:
                raise InvalidReplyRequest("This conversation no longer exists.")
            try:
                selected = choose(current)
            except QueueError as error:
                raise InvalidReplyRequest(str(error)) from error
            message = NewMessage(
                "\n\n".join(item.text for item in selected if item.text),
                attachment_ids=tuple(
                    attachment_id for item in selected for attachment_id in (item.attachment_ids or [])
                ),
                queued_item_ids=tuple(item.id for item in selected),
                handoff_job_id=handoff_job_id,
            )
            return self._send_reserved(session, current, message, reservation)

    def retry_user_message(self, session: Session, conversation: Conversation, message_id: int) -> ReplyJob:
        """Start a new reply to an already stored user message without creating a duplicate message."""
        with self.services.replies.reserve(conversation.id) as reservation:
            current = session.get(Conversation, conversation.id, populate_existing=True)
            message = session.get(Message, message_id)
            if current is None or message is None or message.conversation_id != conversation.id or message.role != "user":
                raise InvalidReplyRequest("That stored user message cannot be retried.")
            if current.head_message_id != message.id:
                raise InvalidReplyRequest("Only the latest user message can be retried.")
            setup = self.prepare(session, current, message_text(message))
            request = ReplyRequest(current, message, read_only=is_read_only_check(message.content))
            return self.launch(session, setup, request, reservation)

    def schedule_send_now(self, conversation_id: int, job_id: str) -> None:
        """Run a saved send-now selection after its specific reply releases the conversation."""
        BACKGROUND_WORK.start(self._send_now_after_job(conversation_id, job_id), name=f"queue-send-now-{job_id}")

    async def _send_now_after_job(self, conversation_id: int, job_id: str) -> None:
        while self.services.replies.busy(conversation_id):
            await asyncio.sleep(0.05)
        try:
            await anyio.to_thread.run_sync(partial(self._send_now_selection, conversation_id, job_id))
        except Exception:  # A background handoff must return its selection to the queue after any failure.
            logger.exception("A durable send-now handoff could not be started; its messages remain queued.")
            with self.services.session_factory() as session:
                session.execute(
                    update(QueuedMessage)
                    .where(
                        QueuedMessage.conversation_id == conversation_id,
                        QueuedMessage.send_now_job_id == job_id,
                    )
                    .values(send_now_job_id=None)
                )
                session.commit()

    def _send_now_selection(self, conversation_id: int, job_id: str) -> None:
        with self.services.session_factory() as session:
            ids = list(
                session.scalars(
                    select(QueuedMessage.id)
                    .where(
                        QueuedMessage.conversation_id == conversation_id,
                        QueuedMessage.send_now_job_id == job_id,
                    )
                    .order_by(QueuedMessage.position, QueuedMessage.id)
                )
            )
            if not ids:
                return
            conversation = session.get(Conversation, conversation_id)
            if conversation is None:
                return
            self.send_queued_ids(session, conversation, ids, job_id)

    def _send_reserved(
        self, session: Session, conversation: Conversation, message: NewMessage, reservation: Reservation
    ) -> ReplyStream:
        setup = self.prepare(session, conversation, message.text)
        # /compact [focus] summarises now instead of replying, unless the Blueprint has its own command.
        commands = setup.agent.commands if setup.agent else None
        own_command = expand_command(setup.layers, message.text, commands) is not None
        focus = compact_focus(message.text) if not own_command else None
        queued = QueuedCommand(message.queued_item_ids, message.handoff_job_id)
        if focus is not None:
            return ReplyStream(self.commands.compact(session, conversation, focus, queued, reservation))
        # /eval [case] starts an eval run of the project's cases instead of replying, unless the Blueprint has its own.
        evaluated = EVAL_COMMAND.match(message.text.strip()) if not own_command else None
        if evaluated is not None:
            return ReplyStream(self.commands.evaluate(session, conversation, evaluated.group(1), queued))
        # Writing in an archived conversation brings it back.
        conversation.archived_at = None
        parent_id, carried = self.edit_point(session, conversation, message.edit_of)
        try:
            new_attachments = claim_attachments(session, conversation, list(message.attachment_ids))
        except AttachmentError as error:
            raise InvalidReplyRequest(str(error)) from error
        first_exchange = conversation.head_message_id is None
        extra_blocks, read_only = command_blocks(session, conversation, setup, message.text, parent_id)
        text = message.text.strip()
        if message.queued_item_ids:
            selected = list(
                session.scalars(
                    select(QueuedMessage).where(
                        QueuedMessage.conversation_id == conversation.id,
                        QueuedMessage.id.in_(message.queued_item_ids),
                    )
                )
            )
            if {item.id for item in selected} != set(message.queued_item_ids):
                raise InvalidReplyRequest("A selected queued message changed before it could be accepted.")
            for item in selected:
                session.delete(item)
        user_message = add_user_message(
            session, conversation, text, [*carried, *new_attachments], extra_blocks, parent_id=parent_id
        )
        title_text = None
        if first_exchange:
            title_text = text or ", ".join(attachment.filename for attachment in new_attachments)
        job = self.launch(session, setup, ReplyRequest(conversation, user_message, title_text, read_only), reservation)
        return ReplyStream(job.follow(), job)

    def edit_point(
        self, session: Session, conversation: Conversation, edit_of: int | None
    ) -> tuple[int | None, list[Attachment]]:
        """Where the new message goes, and the files it keeps from the message it replaces."""
        if edit_of is None:
            return conversation.head_message_id, []
        original = session.get(Message, edit_of)
        if original is None or original.conversation_id != conversation.id or original.role != "user":
            raise InvalidReplyRequest("Only your own messages in this conversation can be edited.")
        carried = [
            attachment
            for block in original.content
            if block.get("type") == "attachment"
            and (attachment := session.get(Attachment, block.get("attachment_id"))) is not None
        ]
        return original.parent_id, carried

    def regenerate(self, session: Session, conversation: Conversation, message_id: int) -> ReplyJob:
        """A new version of a reply: the same question answered again, next to the earlier answer."""
        with self.services.replies.reserve(conversation.id) as reservation:
            current = session.get(Conversation, conversation.id, populate_existing=True)
            if current is None:
                raise InvalidReplyRequest("This conversation no longer exists.")
            return self._regenerate_reserved(session, current, message_id, reservation)

    def _regenerate_reserved(
        self, session: Session, conversation: Conversation, message_id: int, reservation: Reservation
    ) -> ReplyJob:
        reply = session.get(Message, message_id)
        if (
            reply is None
            or reply.conversation_id != conversation.id
            or reply.role != "assistant"
            or reply.parent_id is None
        ):
            raise InvalidReplyRequest("Only a reply in this conversation can be made again.")
        question = session.get_one(Message, reply.parent_id)
        setup = self.prepare(session, conversation, message_text(question))
        conversation.head_message_id = question.id
        session.commit()
        return self.launch(
            session, setup, ReplyRequest(conversation, question, read_only=is_read_only_check(question.content)), reservation
        )

    def send_goal_turn(
        self,
        conversation_id: int,
        resuming: bool = False,
        goal_id: int | None = None,
        reservation: Reservation | None = None,
    ) -> bool:
        """Send the next turn of the conversation's goal, when it goes on; with goal_id only of that goal. Runs
        in a worker thread."""
        with self.services.session_factory() as session:
            if session.scalar(
                select(QueuedMessage.id).where(
                    QueuedMessage.conversation_id == conversation_id,
                    QueuedMessage.send_now_job_id.is_not(None),
                )
            ) is not None:
                raise ReplyBusy("A saved send-now handoff takes precedence over the goal's next turn.")
            reservation = reservation or self.reserve(conversation_id)
            with reservation:
                turn = next_turn(session, conversation_id, resuming, goal_id)
                if turn is None:
                    return False
                conversation = session.get_one(Conversation, conversation_id, populate_existing=True)
                try:
                    setup = self.prepare(session, conversation, turn.text)
                except ReplyError as error:
                    end_goal(session.get_one(Goal, turn.goal_id), "stopped", str(error))
                    session.commit()
                    return False
                block = goal_block(turn.text, turn.turn, turn.max_turns)
                user_message = add_user_message(session, conversation, "", extra_blocks=[block])
                self.launch(session, setup, ReplyRequest(conversation, user_message), reservation)
                return True

    async def send_goal_turn_later(self, conversation_id: int, goal_id: int | None) -> None:
        """After a reply: the next turn of the goal it worked for (goal_id), or, when that goal ended, its review."""
        replies = self.services.replies
        sent = False
        while goal_id is not None:
            while replies.busy(conversation_id):
                await asyncio.sleep(0.05)
            with self.services.session_factory() as session:
                goal = session.get(Goal, goal_id)
                if goal is None or goal.conversation_id != conversation_id or goal.status != "active":
                    break
            try:
                sent = await anyio.to_thread.run_sync(
                    partial(self.send_goal_turn, conversation_id, goal_id=goal_id)
                )
                break
            except ReplyBusy:
                # A new reply reserved the conversation between the idle check and our reservation attempt.
                await asyncio.sleep(0.05)
                continue
            except Exception:  # A background task: the goal stays open and the failure is logged.
                logger.exception("Sending the next turn of a goal failed.")
                return
        if not sent:
            try:
                await ReviewService(self.services).after_goal_of(conversation_id)
            except Exception:  # A background task: the goal has ended; a failed review is logged.
                logger.exception("Reviewing the changes of a goal failed.")
