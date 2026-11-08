"""Stopping the replies and the work they leave running, when YAAP shuts down, before the database engine goes
(background.py says why)."""

from app.core.replies import ReplyRegistry
from app.core.reply_events import BACKGROUND_WORK
from app.reviews.service import REVIEWS

# A goal's next turn stopped while its worker thread runs finishes that thread first, which may launch the next
# reply; the second round stops that reply. A stopped reply starts no further work.
ROUNDS = 2


async def stop_replies_and_their_work(replies: ReplyRegistry) -> None:
    for _ in range(ROUNDS):
        await replies.shutdown()
        await BACKGROUND_WORK.stop()
        await REVIEWS.stop()
