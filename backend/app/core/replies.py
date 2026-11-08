"""Replies run in the background, apart from the browser connection that asked for them.

A reply keeps going when the user opens another page or closes the tab. Every server-sent event it produces is
kept until the reply ends, so a page that comes back can follow it again from the start. Only an explicit stop, or
YAAP shutting down, ends a reply early; what arrived is then stored as incomplete.
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

import anyio

from app.core.background import BackgroundWork

logger = logging.getLogger(__name__)


class ReplyJob:
    def __init__(self, conversation_id: int, environment_id: int) -> None:
        self.conversation_id = conversation_id
        self.environment_id = environment_id
        self.events: list[str] = []
        self.done = False
        self.task: asyncio.Task[None] | None = None
        self._changed = asyncio.Event()

    def _notify(self) -> None:
        changed, self._changed = self._changed, asyncio.Event()
        changed.set()

    async def run(self, events: AsyncIterator[str]) -> None:
        """Until the reply ends. A stop cancels the reply's scope: the cancellation goes through the reply stream, which
        stores what arrived on its way out, and ends at the scope."""
        try:
            async for event in events:
                self.events.append(event)
                self._notify()
        except Exception:  # A background task: the reply's failure is logged, the app goes on.
            logger.exception("A reply failed in the background.")
        finally:
            # Shielded: a stopped reply's scope keeps cancelling every await until the reply has ended.
            with anyio.CancelScope(shield=True), contextlib.suppress(Exception):
                await events.aclose()  # type: ignore[attr-defined]  # the reply streams are async generators
            self.done = True
            self._notify()

    async def follow(self) -> AsyncIterator[str]:
        """Every event of the reply: those so far, then new ones as they come, until the reply ends."""
        index = 0
        while True:
            changed = self._changed
            while index < len(self.events):
                yield self.events[index]
                index += 1
            if self.done:
                return
            await changed.wait()


class ReplyRegistry:
    def __init__(self) -> None:
        self.jobs: dict[int, ReplyJob] = {}
        # Each reply runs in a cancel scope of its own, so a stop waits for a worker thread the reply is in (a tool's,
        # or recording a step) instead of leaving it running.
        self.work: BackgroundWork[None] = BackgroundWork()

    def running(self, conversation_id: int) -> ReplyJob | None:
        return self.jobs.get(conversation_id)

    def running_in(self, environment_id: int) -> list[int]:
        return sorted(job.conversation_id for job in self.jobs.values() if job.environment_id == environment_id)

    def start(self, conversation_id: int, environment_id: int, events: AsyncIterator[str]) -> ReplyJob:
        """Must run on the event loop. The caller has checked that nothing runs for the conversation."""
        job = ReplyJob(conversation_id, environment_id)
        self.jobs[conversation_id] = job
        job.task = self.work.start(job.run(events), name=f"reply-{conversation_id}")
        job.task.add_done_callback(lambda _task: self.jobs.pop(conversation_id, None))
        return job

    def cancel(self, conversation_id: int) -> asyncio.Task[None] | None:
        """Ask the running reply to stop, without waiting for it; must run on the event loop. The reply stops at its
        next wait, as with stop. Returns the reply's task, or None when nothing runs."""
        job = self.jobs.get(conversation_id)
        if job is None or job.task is None:
            return None
        self.work.cancel(job.task)
        return job.task

    async def stop(self, conversation_id: int) -> bool:
        task = self.cancel(conversation_id)
        if task is None:
            return False
        await self.work.wait(task)
        return True

    async def shutdown(self) -> None:
        for conversation_id in list(self.jobs):
            await self.stop(conversation_id)
