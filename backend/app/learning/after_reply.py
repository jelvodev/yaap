"""A review after a goal or a scheduled task ends.

After a reply in a project conversation in which a goal ended (a scheduled task's run is a goal-mode run, so it
counts too), the conversation's signals since its last review are turned into proposals in the background. Without a
new signal there is nothing to review, so no utility call is made. The review follows the switch of memory
suggestions, which eval plays and replays turn off.

The same place watches for the project's Current state: shortly after a reply, a goal that ended refreshes it; when
the conversation then stays quiet, a long conversation refreshes it (`projects/state_refresh.py` decides when).
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import anyio
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.background import BackgroundWork
from app.core.reply_services import ReplyServices
from app.learning.proposals import has_signals
from app.learning.service import LearningService
from app.projects.state_refresh import (
    QUIET_MINUTES,
    RefreshSource,
    StateRefreshContext,
    goal_source,
    quiet_source,
    refresh_state,
)
from app.storage.models import Conversation, Goal

logger = logging.getLogger(__name__)

ENDED_GOALS = ("done", "blocked", "limit")
# Reviews and current-state watches started after replies.
LEARNING_REVIEWS: BackgroundWork[None] = BackgroundWork()
# Time for the goal's follow-up to end the goal (a turn or cost limit) before the watch looks.
SETTLE_SECONDS = 5.0
# The newest watch per conversation; a new reply replaces it.
STATE_WATCHES: dict[int, asyncio.Task[None]] = {}


def review_due(session_factory: sessionmaker[Session], conversation_id: int) -> bool:
    """Whether a goal of the project conversation ended since its last review and new steps carry a signal."""
    with session_factory() as session:
        conversation = session.get(Conversation, conversation_id)
        if conversation is None or conversation.project_id is None:
            return False
        since = conversation.learning_checked_at
        goals = select(func.count()).select_from(Goal).where(
            Goal.conversation_id == conversation_id, Goal.status.in_(ENDED_GOALS), Goal.finished_at.is_not(None)
        )
        if since is not None:
            goals = goals.where(Goal.finished_at > since)
        if not session.scalar(goals):
            return False
        return has_signals(session, conversation_id, since)


async def with_learning_review(
    services: ReplyServices, conversation_id: int, events: AsyncIterator[str]
) -> AsyncIterator[str]:
    """The reply's events; afterwards, when a goal ended with new signals, a review in the background. Its proposals
    wait in the project panel's Learning tab."""
    async for event in events:
        yield event
    if not services.memory_suggestions:
        return
    watch_current_state(services, conversation_id)
    if not await anyio.to_thread.run_sync(review_due, services.session_factory, conversation_id):
        return
    service = LearningService(services.utility, services.blueprint_dir, services.workspace_dir)

    async def run() -> None:
        try:
            await service.review(conversation_id, since_last=True)
        except Exception:  # A background task: a failure is logged and the user's work goes on.
            logger.exception("The learning review failed.")

    LEARNING_REVIEWS.start(run())


def watch_current_state(services: ReplyServices, conversation_id: int) -> None:
    """After a reply in a project conversation: replace the conversation's watch with a new one. On the event loop."""
    previous = STATE_WATCHES.pop(conversation_id, None)
    if previous is not None:
        LEARNING_REVIEWS.cancel(previous)
    context = StateRefreshContext(services.utility, services.workspace_dir)
    task = LEARNING_REVIEWS.start(state_watch(services, context, conversation_id))
    STATE_WATCHES[conversation_id] = task

    def forget(done: asyncio.Task[None]) -> None:
        if STATE_WATCHES.get(conversation_id) is done:
            del STATE_WATCHES[conversation_id]

    task.add_done_callback(forget)


async def state_watch(services: ReplyServices, context: StateRefreshContext, conversation_id: int) -> None:
    """Refresh the current state after a goal ended, else once the conversation has been quiet long enough. A reply
    that starts in the meantime has a watch of its own, so this one stops."""
    try:
        await asyncio.sleep(SETTLE_SECONDS)
        if services.replies.busy(conversation_id):
            return
        source: RefreshSource | None = await anyio.to_thread.run_sync(
            goal_source, context, conversation_id, datetime.now(UTC)
        )
        if source is None:
            await asyncio.sleep(QUIET_MINUTES * 60 - SETTLE_SECONDS)
            if services.replies.busy(conversation_id):
                return
            source = await anyio.to_thread.run_sync(quiet_source, context, conversation_id, datetime.now(UTC))
        if source is not None:
            await refresh_state(context, source)
    except Exception:  # A background task: a failed refresh is logged and the old state stays.
        logger.exception("Refreshing the current state failed.")
