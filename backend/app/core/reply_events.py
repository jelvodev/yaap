"""What happens around a reply's event stream: work that waits for the reply to end.

Each wrapper passes the reply's events on unchanged and acts when the stream ends: steering that came too late is
queued, the summary and memory checks start, the search index takes in the new messages, and an open goal goes on.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import anyio

from app.agent.runs import AgentRun
from app.core.background import BackgroundWork
from app.core.chat_events import merge_events, server_sent_event
from app.core.goals import ACTIVE, open_goal
from app.core.message_queue import queue_undelivered_steering
from app.core.reply_services import ReplyServices
from app.core.summaries import start_background_summary, summarise_if_needed
from app.core.builtin_prompts import utility_instructions
from app.core.utility import propose_title
from app.projects.consolidation import start_tidy_after_reply
from app.projects.service import project_path
from app.projects.suggestions import due, suggest, suggest_with_title
from app.storage.models import Conversation, Project
from app.tracing.route_feed import RemovedStop, StageFeed
from app.tracing.route_schema import RouteStopOut

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TitleRequest:
    """A new conversation whose first reply gets a title."""

    environment_id: int
    conversation_id: int
    # The first message (or its files' names) the title is written from.
    title_text: str
    reply_language: str = ""

# Background work started after replies.
BACKGROUND_WORK: BackgroundWork[None] = BackgroundWork()


ENDING_EVENTS = ("event: done", "event: error")


async def with_stage_events(feed: StageFeed, events: AsyncIterator[str]) -> AsyncIterator[str]:
    """The reply's events with the route's stops (event "stage") as they are drawn, also while the reply waits.
    The approval request and its answer pass through here, so the feed learns of a waiting call without the agent
    knowing of the route."""
    queue = feed.open()
    async for item in merge_events(stops_before_the_end(queue, events), queue):
        if isinstance(item, str):
            tell_feed(feed, item)
            yield item
        else:
            yield stage_event(item)


async def stops_before_the_end(
    queue: "asyncio.Queue[RouteStopOut | RemovedStop]", events: AsyncIterator[str]
) -> AsyncIterator[str]:
    """The reply's events; stops still queued when the reply ends go out just before its end event, so the drawing is
    whole when the end arrives. Done here, ahead of the merge, because the merge cannot put them before an event it
    already holds."""
    async for event in events:
        if event.startswith(ENDING_EVENTS):
            while not queue.empty():
                yield stage_event(queue.get_nowait())
        yield event


def stage_event(item: RouteStopOut | RemovedStop) -> str:
    if isinstance(item, RemovedStop):
        return server_sent_event("stage_removed", {"id": item.id})
    return server_sent_event("stage", item.model_dump(mode="json"))


def tell_feed(feed: StageFeed, event: str) -> None:
    head, _, data = event.partition("\ndata: ")
    if head == "event: approval_resolved":
        feed.answered()
    elif head == "event: approval_request":
        try:
            request = json.loads(data)
        except ValueError:
            return
        summary = request.get("summary")
        # A helper's request names who asks in brackets; its stop is not the reply's own.
        if isinstance(summary, str) and not summary.startswith("["):
            feed.waiting(str(request.get("tool", "tool")), summary)


async def with_steering_fallback(
    services: ReplyServices, conversation_id: int, run: AgentRun, events: AsyncIterator[str]
) -> AsyncIterator[str]:
    """The reply's events; a steering message the agent did not get in time is queued for after the reply."""
    try:
        async for event in events:
            yield event
    finally:
        if run.state.record_id is not None and run.services.steering is not None:
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(run.services.steering.restore, run.state.record_id)
            run.state.steering_item_ids = []
        if run.state.steering:
            texts, run.state.steering = run.state.steering, []
            # Shielded: a stopped reply's scope keeps cancelling every await until the reply has ended.
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(
                    queue_undelivered_steering, services.session_factory, conversation_id, texts
                )


async def with_memory_suggestions(
    services: ReplyServices, conversation_id: int, events: AsyncIterator[str]
) -> AsyncIterator[str]:
    """The reply's events; afterwards, when a goal ended or ten turns passed, memory suggestions in the background.
    They wait in the Memory tab; nothing is written without the user."""
    async for event in events:
        yield event
    if services.memory_tidy:
        start_tidy_after_reply(services.utility, services.workspace_dir, conversation_id)
    folder = memory_folder(services, conversation_id) if services.memory_suggestions else None
    if folder is None:
        return
    context = services.utility

    async def run() -> None:
        try:
            await suggest(context, conversation_id, folder)
        except Exception:  # A background task: a failure is logged and the user's work goes on.
            logger.exception("Memory suggestions failed.")

    BACKGROUND_WORK.start(run())


def memory_folder(services: ReplyServices, conversation_id: int) -> Path | None:
    """The project folder when memory suggestions are due for the conversation now; None when they are not."""
    with services.session_factory() as session:
        if not due(session, conversation_id):
            return None
        conversation = session.get_one(Conversation, conversation_id)
        project = session.get(Project, conversation.project_id)
        return project_path(services.workspace_dir, project) if project is not None else None


async def first_title(services: ReplyServices, conversation: TitleRequest, reply_text: str) -> str | None:
    """The title of a new conversation after its first reply. When memory suggestions are due after the same reply
    (a goal that ended in it), one utility call brings both; the conversation then counts as checked, so
    with_memory_suggestions makes no second call. Otherwise, or when that call brings no title, the title comes
    alone. Either works without the other: no title without utility titles, suggestions alone without them."""
    context, conversation_id, language = services.utility, conversation.conversation_id, conversation.reply_language
    folder = memory_folder(services, conversation_id) if services.memory_suggestions else None
    if folder is not None:
        title = await suggest_with_title(context, conversation_id, folder, language)
        if title is not None:
            return title
    title_instructions = utility_instructions(context, conversation.environment_id, "memory-keeper", "Title")
    return await propose_title(
        context,
        conversation.environment_id,
        conversation_id,
        conversation.title_text,
        reply_text,
        language,
        title_instructions,
    )


async def with_summary_check(
    services: ReplyServices, conversation_id: int, events: AsyncIterator[str]
) -> AsyncIterator[str]:
    """The reply's events; afterwards, when the context is filling up, a summary of the earlier messages. It runs in
    the background, so the reply is done at once; tests can run it inline."""
    try:
        async for event in events:
            yield event
    finally:
        if services.background_summaries:
            start_background_summary(services.utility, conversation_id)
        else:
            await summarise_if_needed(services.utility, conversation_id)


async def with_index_nudge(services: ReplyServices, events: AsyncIterator[str]) -> AsyncIterator[str]:
    """The reply's events; afterwards the search index takes in the new messages."""
    try:
        async for event in events:
            yield event
    finally:
        if services.indexer is not None:
            services.indexer.nudge()


async def with_goal_follow_up(
    services: ReplyServices,
    conversation_id: int,
    events: AsyncIterator[str],
    next_turn: Callable[[int, int | None], Awaitable[None]],
) -> AsyncIterator[str]:
    """The reply's events; afterwards the next turn of an open goal. A reply the user stops pauses the goal.

    The follow-up is told which goal the reply worked for (None when none is active any more): it waits until
    the reply has left the registry, and a newer goal may have started in the conversation by then."""
    try:
        async for event in events:
            yield event
    except asyncio.CancelledError:
        with services.session_factory() as session:
            goal = open_goal(session, conversation_id)
            if goal is not None and goal.status == ACTIVE:
                goal.status = "paused"
                session.commit()
        raise
    with services.session_factory() as session:
        goal = open_goal(session, conversation_id)
        goal_id = goal.id if goal is not None and goal.status == ACTIVE else None
    BACKGROUND_WORK.start(next_turn(conversation_id, goal_id))
