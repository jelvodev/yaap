"""The commands a message can be instead of a question: /compact, /eval, /check, /plan, /card and /goal.

/compact and /eval do not start a reply. When they were sent from the queue, their rows leave the queue only once the
command was accepted: /eval when its run has started, /compact when the summary has been made or its failure
reported. A command that is refused, or that is cut short, stays queued.
"""

import uuid
import weakref
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

import anyio
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.blueprint.check import CheckError, check_block
from app.board.card_command import card_command
from app.board.service import BoardService
from app.core.chat_events import server_sent_event
from app.core.chat_history import load_branch
from app.core.goals import GoalError, create_goal, goal_block, goal_objective, start_text
from app.core.message_parts import expand_command, skill_blocks
from app.core.queue_commands import claim_eval, claim_items, consume_items, release_items
from app.core.reply_errors import CommandRefused
from app.core.replies import ReplyRegistry, Reservation
from app.core.reply_event_models import CompactFailedEvent, EvalStartedEvent
from app.core.reply_services import ChatEvals, ReplyServices
from app.core.reply_setup import ReplySetup
from app.core.summaries import TOO_LITTLE, compact_events, cover_point, yaap_compacts
from app.plans.command import plan_command
from app.storage.models import Conversation


@dataclass(frozen=True)
class QueuedCommand:
    """The queue rows a command was sent from, and the send-now job that already holds them, if any."""

    item_ids: tuple[int, ...] = ()
    handoff_job_id: str | None = None


@dataclass(frozen=True)
class CommandContext:
    """What a built-in command works with: the reply's session and conversation, its setup and the board."""

    session: Session
    conversation: Conversation
    setup: ReplySetup
    board: BoardService | None = None


# The built-in commands that make a read-only reply, in the order they are tried. Each returns its command block, or
# None when the message is not that command, and raises CommandRefused when it cannot run here.
BuiltinCommand = Callable[[CommandContext, str], dict[str, Any] | None]


def check_command(context: CommandContext, text: str) -> dict[str, Any] | None:
    """/check checks the project against its standards in a read-only run."""
    try:
        block = check_block(context.setup.layers, text)
    except CheckError as error:
        raise CommandRefused(str(error)) from error
    if block is not None and context.setup.agent_project is None:
        raise CommandRefused("/check works in a project conversation.")
    return block


def plan_builtin(context: CommandContext, text: str) -> dict[str, Any] | None:
    """/plan makes a plan in a read-only run (app/plans/command.py)."""
    return plan_command(context.setup, text)


def card_builtin(context: CommandContext, text: str) -> dict[str, Any] | None:
    """/card makes a card on the project's board; the reply confirms it (app/board/card_command.py)."""
    return card_command(context.board, context.session, context.conversation, text)


BUILTIN_COMMANDS: tuple[BuiltinCommand, ...] = (check_command, plan_builtin, card_builtin)


def builtin_block(context: CommandContext, text: str) -> dict[str, Any] | None:
    """The block of the built-in command the message is, if any; a refusal rolls the session back first."""
    session = context.session
    for command in BUILTIN_COMMANDS:
        try:
            block = command(context, text)
        except CommandRefused:
            session.rollback()
            raise
        if block is not None:
            return block
    return None


def command_blocks(
    session: Session,
    conversation: Conversation,
    setup: ReplySetup,
    text: str,
    parent_id: int | None,
    board: BoardService | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """The blocks a message gets besides its text (pinned skills, a command, a goal), and whether the reply is read
    only. A Blueprint command of the same name wins over a built-in one."""
    earlier = load_branch(session, conversation.id, parent_id)
    extra_blocks = skill_blocks(
        setup.layers,
        conversation.pinned_skills or [],
        [message.content for message in earlier if message.role == "user"],
    )
    command = expand_command(setup.layers, text, setup.agent.commands if setup.agent else None)
    if command is None:
        command = builtin_block(CommandContext(session, conversation, setup, board), text)
    if command is not None:
        extra_blocks.append(command)
        return extra_blocks, command.get("read_only") is True
    # /goal starts goal mode, unless the Blueprint has its own command named goal.
    try:
        objective = goal_objective(text)
        if objective is not None:
            goal = create_goal(session, conversation, objective)
            extra_blocks.append(goal_block(start_text(objective, goal.max_turns), 1, goal.max_turns))
    except GoalError as error:
        session.rollback()
        raise CommandRefused(str(error)) from error
    return extra_blocks, False


def _consume(session_factory: sessionmaker[Session], conversation_id: int, queued: QueuedCommand) -> None:
    with session_factory() as session:
        consume_items(session, conversation_id, queued.item_ids)


async def claimed_compact_events(
    session_factory: sessionmaker[Session],
    conversation_id: int,
    queued: QueuedCommand,
    events: AsyncIterator[str],
) -> AsyncIterator[str]:
    """The events of a queued /compact. It runs only when its row can be claimed, so a second sender, or a row that
    was removed meanwhile, does not run it again; the row leaves the queue when the command ended, and goes back to it
    when the stream is cut off before."""
    token = queued.handoff_job_id or str(uuid.uuid4())
    claim = partial(claim_items, session_factory, conversation_id, queued.item_ids, token, queued.handoff_job_id)
    if not await anyio.to_thread.run_sync(claim):
        yield server_sent_event(CompactFailedEvent(detail="That queued command is no longer waiting."))
        return
    ended = False
    try:
        async for event in events:
            yield event
        ended = True
    finally:
        # Shielded: a client that left cancels the stream, and the row must still be settled.
        with anyio.CancelScope(shield=True):
            if ended:
                await anyio.to_thread.run_sync(partial(_consume, session_factory, conversation_id, queued))
            else:
                await anyio.to_thread.run_sync(partial(release_items, session_factory, conversation_id, token))


async def holding_reservation(
    registry: ReplyRegistry, reservation: Reservation, events: AsyncIterator[str]
) -> AsyncIterator[str]:
    """The events of a command that keeps the conversation reserved until they end, like a reply does."""
    try:
        async for event in events:
            yield event
    finally:
        registry.release(reservation)


def hold_for_stream(
    registry: ReplyRegistry, reservation: Reservation, events: AsyncIterator[str]
) -> AsyncIterator[str]:
    """Keep the reservation the sender holds for as long as `events` runs, instead of letting its `with` release it
    when the stream is only returned. Marking it consumed makes that `with` leave it alone; the stream releases it
    when it ends or is closed, and a stream that is dropped before it ever started releases it too."""
    reservation.consumed = True
    held = holding_reservation(registry, reservation, events)
    weakref.finalize(held, registry.release, reservation)
    return held


class ReplyCommands:
    def __init__(self, services: ReplyServices) -> None:
        self.services = services

    def compact(
        self, session: Session, conversation: Conversation, focus: str, queued: QueuedCommand, reservation: Reservation
    ) -> AsyncIterator[str]:
        """/compact: the summary is made while the stream is open; nothing else is added to the conversation."""
        if not yaap_compacts(session, self.services.providers, conversation):
            raise CommandRefused("Claude Code and Codex compact their own sessions.")
        if cover_point(load_branch(session, conversation.id, conversation.head_message_id)) is None:
            raise CommandRefused(TOO_LITTLE)
        conversation_id = conversation.id
        session.close()
        events = compact_events(self.services.utility, conversation_id, focus)
        if queued.item_ids:
            events = claimed_compact_events(self.services.session_factory, conversation_id, queued, events)
        return hold_for_stream(self.services.replies, reservation, events)

    def queued_eval_run(
        self,
        session: Session,
        evals: ChatEvals,
        conversation: Conversation,
        case_name: str | None,
        queued: QueuedCommand,
    ) -> tuple[int, str]:
        """The run of /eval and what the chat shows. A queued command starts at most one run: a second consumer, or
        one after a restart, gets the run that came from the item."""
        if not queued.item_ids:
            return evals.start_from_chat(conversation, case_name)
        item_id = queued.item_ids[0]
        claim, existing = claim_eval(session, conversation.id, item_id, queued.handoff_job_id)
        if claim == "start":
            try:
                run_id, text = evals.start_from_chat(conversation, case_name, item_id)
            except IntegrityError:
                # Another consumer created the run for this item first (unique queue_item_id).
                session.rollback()
                claim, existing = claim_eval(session, conversation.id, item_id, queued.handoff_job_id)
            else:
                # The run's creation removed the first row; any other rows of the command go now.
                consume_items(session, conversation.id, queued.item_ids)
                return run_id, text
        if claim == "existing" and existing is not None:
            return existing, f"Eval run {existing} was already started from this command."
        raise CommandRefused("That queued command is no longer waiting.")

    def evaluate(
        self, session: Session, conversation: Conversation, case_name: str | None, queued: QueuedCommand
    ) -> AsyncIterator[str]:
        """/eval: the run starts in the background; the stream says which run it is. Nothing is added to the
        conversation."""
        evals = self.services.evals
        if evals is None or conversation.project_id is None:
            raise CommandRefused("/eval runs the eval cases of a project; use it in a project conversation.")
        run_id, text = self.queued_eval_run(session, evals, conversation, case_name, queued)
        project_id = conversation.project_id

        async def events() -> AsyncIterator[str]:
            yield server_sent_event(EvalStartedEvent(run_id=run_id, project_id=project_id, detail=text))

        return events()
