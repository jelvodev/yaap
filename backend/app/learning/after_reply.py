"""A review after a goal or a scheduled task ends.

After a reply in a project conversation in which a goal ended (a scheduled task's run is a goal-mode run, so it
counts too), the conversation's signals since its last review are turned into proposals in the background. Without a
new signal there is nothing to review, so no utility call is made. The review follows the switch of memory
suggestions, which eval plays and replays turn off.
"""

import asyncio
import logging
from collections.abc import AsyncIterator

import anyio
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.reply_services import ReplyServices
from app.learning.proposals import has_signals
from app.learning.service import LearningService
from app.storage.models import Conversation, Goal

logger = logging.getLogger(__name__)

ENDED_GOALS = ("done", "blocked", "limit")
BACKGROUND_REVIEWS: set[asyncio.Task[None]] = set()


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
    if not await anyio.to_thread.run_sync(review_due, services.session_factory, conversation_id):
        return
    service = LearningService(services.utility, services.blueprint_dir, services.workspace_dir)

    async def run() -> None:
        try:
            await service.review(conversation_id, since_last=True)
        except Exception:  # A background task: a failure is logged and the user's work goes on.
            logger.exception("The learning review failed.")

    task = asyncio.get_running_loop().create_task(run())
    BACKGROUND_REVIEWS.add(task)
    task.add_done_callback(BACKGROUND_REVIEWS.discard)
