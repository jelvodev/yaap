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
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, replace
from functools import partial
from typing import Any

import anyio
from sqlalchemy.orm import Session

from app.agent.runs import AgentRun
from app.agent.tool_catalog import with_listing
from app.agent.toolbox import RunToolBox
from app.attachments.files import AttachmentError
from app.attachments.service import AttachmentResolver, claim_attachments, conversation_attachments
from app.blueprint.check import CheckError, check_block
from app.core.audit import record_request, reply_entry, with_refusal
from app.core.cache_policy import reply_cache_ttl
from app.core.budget_stop import both_listeners, reply_budget_guard, with_budget_stop
from app.core.chat import add_user_message, build_request, load_branch, server_sent_event, stream_reply
from app.core.context_control import output_tokens
from app.core.goals import GoalError, create_goal, end_goal, goal_block, goal_objective, next_turn, start_text
from app.core.message_parts import expand_command, skill_blocks
from app.core.model_choice import Billing
from app.core.outgoing_checks import checked_provider, request_refusal
from app.core.replies import ReplyJob
from app.core.reply_errors import CommandRefused, InvalidReplyRequest, ReplyError
from app.core.reply_events import (
    TitleRequest,
    first_title,
    with_goal_follow_up,
    with_index_nudge,
    with_memory_suggestions,
    with_steering_fallback,
    with_summary_check,
)
from app.core.reply_repo_map import with_repo_map
from app.core.reply_routing import route_reply
from app.core.reply_run import reply_model_trace, run_folder, start_agent_run
from app.core.reply_services import ReplyServices
from app.core.reply_setup import ReplySetup, prepare_reply
from app.core.search_index import message_text
from app.core.summaries import TOO_LITTLE, compact_events, compact_focus, cover_point, yaap_compacts
from app.core.turn_context import with_turn_context
from app.learning.after_reply import with_learning_review
from app.providers.base import AgentRequest, HistoryMessage, ToolServer
from app.reviews.service import ReviewService
from app.storage.models import Attachment, Conversation, Goal, Message
from app.tracing.otlp_export import with_telemetry_export
from app.tracing.provider import traced_provider
from app.tracing.recorder import StepListener

logger = logging.getLogger(__name__)

# /eval, or /eval <case>: an eval run of all the project's cases or of one.
EVAL_COMMAND = re.compile(r"^/eval(?:\s+([a-z0-9][a-z0-9-]{0,59}))?$")


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


@dataclass(frozen=True)
class ReplyStream:
    """What a sent message streams back: the reply's events, or for /compact the summary's events."""

    events: AsyncIterator[str]
    # The running reply; None for /compact.
    job: ReplyJob | None = None


class ReplyService:
    def __init__(self, services: ReplyServices) -> None:
        self.services = services

    def prepare(self, session: Session, conversation: Conversation, query: str = "") -> ReplySetup:
        return prepare_reply(self.services, session, conversation, query)

    def launch(self, session: Session, setup: ReplySetup, request: ReplyRequest) -> ReplyJob:
        """Start the reply to request.user_message in the background; the job can be followed from its start."""
        services, conversation = self.services, request.conversation
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
        entry = reply_entry(conversation, setup.provider_spec, agent_request, setup.project_data, bool(attachments))
        record_request(services.session_factory, with_refusal(entry, request_refusal(agent_request, setup.outgoing)))
        trace = reply_model_trace(services, session, conversation, setup, run, on_step)
        billing: Billing = "metered" if setup.provider_spec.metered else "subscription"
        events = stream_reply(
            services.session_factory,
            services.providers,
            traced_provider(checked_provider(setup.provider_spec.create(setup.api_key), setup.outgoing), trace),
            conversation.id,
            request.user_message,
            agent_request,
            run,
            services.runs,
            self.title_task(conversation, setup, request.title_text),
            record_id=trace.run_id if run is None else None,
            route=setup.route.stored() if setup.route is not None else None,
            billing=billing,
        )
        if guard is not None:
            events = with_budget_stop(services.session_factory, guard, conversation_id, events)
        events = self.after_reply(conversation, run, events)
        events = with_telemetry_export(services.telemetry, trace.run_id, events)
        # The reply runs on its own: whoever started it only follows it, and leaving does not stop it.
        return anyio.from_thread.run_sync(services.replies.start, conversation.id, conversation.environment_id, events)

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
        return run, replace(
            agent_request,
            # The run's outside MCP servers, known once its tools are loaded, cached with the stable parts, and the
            # repository map of the folder it works on.
            system=with_repo_map(services, session, setup, run, with_listing(agent_request.system, run.catalog)),
            working_folder=run_folder(services, setup),
            tool_server=ToolServer(url=f"{services.internal_url}/mcp", token=run.token),
            tool_box=RunToolBox(services.runs, run),
        )

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
        setup = self.prepare(session, conversation, message.text)
        # /compact [focus] summarises now instead of replying, unless the Blueprint has its own command.
        commands = setup.agent.commands if setup.agent else None
        own_command = expand_command(setup.layers, message.text, commands) is not None
        focus = compact_focus(message.text) if not own_command else None
        if focus is not None:
            return ReplyStream(self.compact_now(session, conversation, focus))
        # /eval [case] starts an eval run of the project's cases instead of replying, unless the Blueprint has its own.
        evaluated = EVAL_COMMAND.match(message.text.strip()) if not own_command else None
        if evaluated is not None:
            return ReplyStream(self.eval_now(conversation, evaluated.group(1)))
        # Writing in an archived conversation brings it back.
        conversation.archived_at = None
        parent_id, carried = self.edit_point(session, conversation, message.edit_of)
        try:
            new_attachments = claim_attachments(session, conversation, list(message.attachment_ids))
        except AttachmentError as error:
            raise InvalidReplyRequest(str(error)) from error
        first_exchange = conversation.head_message_id is None
        extra_blocks, read_only = self.command_blocks(session, conversation, setup, message.text, parent_id)
        text = message.text.strip()
        user_message = add_user_message(
            session, conversation, text, [*carried, *new_attachments], extra_blocks, parent_id=parent_id
        )
        title_text = None
        if first_exchange:
            title_text = text or ", ".join(attachment.filename for attachment in new_attachments)
        job = self.launch(session, setup, ReplyRequest(conversation, user_message, title_text, read_only))
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

    def command_blocks(
        self, session: Session, conversation: Conversation, setup: ReplySetup, text: str, parent_id: int | None
    ) -> tuple[list[dict[str, Any]], bool]:
        """The blocks the message gets besides its text (pinned skills, a command, a goal), and whether the reply is
        read only."""
        earlier = load_branch(session, conversation.id, parent_id)
        extra_blocks = skill_blocks(
            setup.layers,
            conversation.pinned_skills or [],
            [message.content for message in earlier if message.role == "user"],
        )
        command = expand_command(setup.layers, text, setup.agent.commands if setup.agent else None)
        read_only = False
        if command is None:
            # /check checks the project against its standards in a read-only run, unless the Blueprint has its own
            # command named check.
            try:
                command = check_block(setup.layers, text)
            except CheckError as error:
                session.rollback()
                raise CommandRefused(str(error)) from error
            if command is not None:
                if setup.agent_project is None:
                    session.rollback()
                    raise CommandRefused("/check works in a project conversation.")
                read_only = True
        if command is not None:
            extra_blocks.append(command)
            return extra_blocks, read_only
        # /goal starts goal mode, unless the Blueprint has its own command named goal.
        try:
            objective = goal_objective(text)
            if objective is not None:
                goal = create_goal(session, conversation, objective)
                extra_blocks.append(goal_block(start_text(objective, goal.max_turns), 1, goal.max_turns))
        except GoalError as error:
            session.rollback()
            raise CommandRefused(str(error)) from error
        return extra_blocks, read_only

    def compact_now(self, session: Session, conversation: Conversation, focus: str) -> AsyncIterator[str]:
        """/compact: the summary is made while the stream is open; nothing else is added to the conversation."""
        if not yaap_compacts(session, self.services.providers, conversation):
            raise CommandRefused("Claude Code and Codex compact their own sessions.")
        if cover_point(load_branch(session, conversation.id, conversation.head_message_id)) is None:
            raise CommandRefused(TOO_LITTLE)
        conversation_id = conversation.id
        session.close()
        return compact_events(self.services.utility, conversation_id, focus)

    def eval_now(self, conversation: Conversation, case_name: str | None) -> AsyncIterator[str]:
        """/eval: the run starts in the background; the stream says which run it is. Nothing is added to the
        conversation."""
        evals = self.services.evals
        if evals is None or conversation.project_id is None:
            raise CommandRefused("/eval runs the eval cases of a project; use it in a project conversation.")
        run_id, text = evals.start_from_chat(conversation, case_name)
        project_id = conversation.project_id

        async def events() -> AsyncIterator[str]:
            yield server_sent_event("eval_started", {"run_id": run_id, "project_id": project_id, "detail": text})

        return events()

    def regenerate(self, session: Session, conversation: Conversation, message_id: int) -> ReplyJob:
        """A new version of a reply: the same question answered again, next to the earlier answer."""
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
        return self.launch(session, setup, ReplyRequest(conversation, question))

    def send_goal_turn(self, conversation_id: int, resuming: bool = False, goal_id: int | None = None) -> bool:
        """Send the next turn of the conversation's goal, when it goes on; with goal_id only of that goal. Runs
        in a worker thread."""
        with self.services.session_factory() as session:
            turn = next_turn(session, conversation_id, resuming, goal_id)
            if turn is None:
                return False
            conversation = session.get_one(Conversation, conversation_id)
            try:
                setup = self.prepare(session, conversation, turn.text)
            except ReplyError as error:
                end_goal(session.get_one(Goal, turn.goal_id), "stopped", str(error))
                session.commit()
                return False
            block = goal_block(turn.text, turn.turn, turn.max_turns)
            user_message = add_user_message(session, conversation, "", extra_blocks=[block])
            self.launch(session, setup, ReplyRequest(conversation, user_message))
            return True

    async def send_goal_turn_later(self, conversation_id: int, goal_id: int | None) -> None:
        """After a reply: the next turn of the goal it worked for (goal_id), or, when that goal ended, its review."""
        replies = self.services.replies
        for _ in range(200):  # The reply that just ended leaves the registry a moment after its last event.
            if replies.running(conversation_id) is None:
                break
            await asyncio.sleep(0.05)
        try:
            sent = goal_id is not None and await anyio.to_thread.run_sync(
                partial(self.send_goal_turn, conversation_id, goal_id=goal_id)
            )
        except Exception:  # A background task: the goal stays open and the failure is logged.
            logger.exception("Sending the next turn of a goal failed.")
            return
        if not sent:
            try:
                await ReviewService(self.services).after_goal_of(conversation_id)
            except Exception:  # A background task: the goal has ended; a failed review is logged.
                logger.exception("Reviewing the changes of a goal failed.")
