"""Stopping the replies and the work they leave running, when YAAP shuts down, before the database engine goes
(background.py says why)."""

from app.core.background import Stoppable
from app.core.replies import ReplyRegistry
from app.core.reply_events import BACKGROUND_WORK
from app.core.summaries import SUMMARIES
from app.learning.after_reply import LEARNING_REVIEWS
from app.projects.consolidation import TIDY_CHECKS
from app.reviews.service import REVIEWS
from app.tracing.otlp_export import EXPORTS

# A goal's next turn stopped while its worker thread runs finishes that thread first, which may launch the next
# reply; the second round stops that reply. A stopped reply starts no further work.
ROUNDS = 2

# Work that replies start, also from the `finally` of a reply that is stopped: stopped after the replies.
AFTER_REPLIES: tuple[Stoppable, ...] = (
    BACKGROUND_WORK,
    REVIEWS,
    SUMMARIES,
    LEARNING_REVIEWS,
    TIDY_CHECKS,
    EXPORTS,
)


async def stop_replies_and_their_work(replies: ReplyRegistry) -> None:
    for _ in range(ROUNDS):
        await replies.shutdown()
        for work in AFTER_REPLIES:
            await work.stop()
