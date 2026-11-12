"""The commands a message can be instead of a question: /compact, /eval, /check and /goal.

/compact and /eval do not start a reply. When they were sent from the queue, their rows leave the queue only once the
command was accepted: /eval when its run has started, /compact when the summary has been made or its failure
reported. A command that is refused, or that is cut short, stays queued.
"""

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from functools import partial
from typing import Any

import anyio
from sqlalchemy.orm import Session, sessionmaker

from app.blueprint.check import CheckError, check_block
from app.core.chat_events import server_sent_event
from app.core.chat_history import load_branch
from app.core.goals import GoalError, create_goal, goal_block, goal_objective, start_text
from app.core.message_parts import expand_command, skill_blocks
from app.core.queue_commands import claim_items, consume_items, release_items
from app.core.reply_errors import CommandRefused
from app.core.reply_services import ReplyServices
from app.core.reply_setup import ReplySetup
from app.core.summaries import TOO_LITTLE, compact_events, cover_point, yaap_compacts
from app.storage.models import Conversation


@dataclass(frozen=True)
class QueuedCommand:
    """The queue rows a command was sent from, and the send-now job that already holds them, if any."""

    item_ids: tuple[int, ...] = ()
    handoff_job_id: str | None = None


def command_blocks(
    session: Session, conversation: Conversation, setup: ReplySetup, text: str, parent_id: int | None
) -> tuple[list[dict[str, Any]], bool]:
    """The blocks a message gets besides its text (pinned skills, a command, a goal), and whether the reply is read
    only."""
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
        yield server_sent_event("compact_failed", {"detail": "That queued command is no longer waiting."})
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


class ReplyCommands:
    def __init__(self, services: ReplyServices) -> None:
        self.services = services

    def compact(
        self, session: Session, conversation: Conversation, focus: str, queued: QueuedCommand
    ) -> AsyncIterator[str]:
        """/compact: the summary is made while the stream is open; nothing else is added to the conversation."""
        if not yaap_compacts(session, self.services.providers, conversation):
            raise CommandRefused("Claude Code and Codex compact their own sessions.")
        if cover_point(load_branch(session, conversation.id, conversation.head_message_id)) is None:
            raise CommandRefused(TOO_LITTLE)
        conversation_id = conversation.id
        session.close()
        events = compact_events(self.services.utility, conversation_id, focus)
        if not queued.item_ids:
            return events
        return claimed_compact_events(self.services.session_factory, conversation_id, queued, events)

    def evaluate(
        self, session: Session, conversation: Conversation, case_name: str | None, queued: QueuedCommand
    ) -> AsyncIterator[str]:
        """/eval: the run starts in the background; the stream says which run it is. Nothing is added to the
        conversation."""
        evals = self.services.evals
        if evals is None or conversation.project_id is None:
            raise CommandRefused("/eval runs the eval cases of a project; use it in a project conversation.")
        run_id, text = evals.start_from_chat(conversation, case_name)
        if queued.item_ids:
            consume_items(session, conversation.id, queued.item_ids)
        project_id = conversation.project_id

        async def events() -> AsyncIterator[str]:
            yield server_sent_event("eval_started", {"run_id": run_id, "project_id": project_id, "detail": text})

        return events()
