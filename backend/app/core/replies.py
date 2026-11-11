"""Replies run in the background, apart from the browser connection that asked for them.

A reply keeps going when the user opens another page or closes the tab. Every server-sent event it produces is
kept until the reply ends, so a page that comes back can follow it again from the start. Only an explicit stop, or
YAAP shutting down, ends a reply early; what arrived is then stored as incomplete.
"""

import asyncio
import contextlib
import logging
import threading
import uuid
from collections.abc import AsyncIterator

import anyio

from app.core.background import BackgroundWork
from app.core.reply_errors import ReplyBusy

logger = logging.getLogger(__name__)


class ReplyJob:
    def __init__(self, conversation_id: int, environment_id: int) -> None:
        self.id = str(uuid.uuid4())
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


class Reservation:
    """A short-lived claim for one conversation, consumed atomically when its reply job starts."""

    def __init__(self, registry: "ReplyRegistry", conversation_id: int) -> None:
        self.registry = registry
        self.conversation_id = conversation_id
        self.consumed = False

    def __enter__(self) -> "Reservation":
        return self

    def __exit__(self, *_: object) -> None:
        self.release()

    def release(self) -> None:
        if not self.consumed:
            self.registry.release(self)


class ReplyRegistry:
    def __init__(self) -> None:
        self.jobs: dict[int, ReplyJob] = {}
        self.reservations: dict[int, Reservation] = {}
        self._lock = threading.Lock()
        # Each reply runs in a cancel scope of its own, so a stop waits for a worker thread the reply is in (a tool's,
        # or recording a step) instead of leaving it running.
        self.work: BackgroundWork[None] = BackgroundWork()

    def running(self, conversation_id: int) -> ReplyJob | None:
        with self._lock:
            return self.jobs.get(conversation_id)

    def running_in(self, environment_id: int) -> list[int]:
        with self._lock:
            return sorted(job.conversation_id for job in self.jobs.values() if job.environment_id == environment_id)

    def busy(self, conversation_id: int) -> bool:
        """Whether a conversation has either a live job or a start in progress."""
        with self._lock:
            return conversation_id in self.jobs or conversation_id in self.reservations

    def reserve(self, conversation_id: int) -> Reservation:
        """Claim a conversation before reading or changing state used to start its reply."""
        with self._lock:
            if conversation_id in self.jobs or conversation_id in self.reservations:
                raise ReplyBusy("YAAP is still replying in this conversation; wait or stop it first.")
            reservation = Reservation(self, conversation_id)
            self.reservations[conversation_id] = reservation
            return reservation

    def release(self, reservation: Reservation) -> None:
        with self._lock:
            if self.reservations.get(reservation.conversation_id) is reservation:
                del self.reservations[reservation.conversation_id]

    def start(self, reservation: Reservation, environment_id: int, events: AsyncIterator[str]) -> ReplyJob:
        """Consume a live reservation and register its job without an unclaimed gap; must run on the event loop."""
        conversation_id = reservation.conversation_id
        with self._lock:
            if reservation.registry is not self or reservation.consumed:
                raise ReplyBusy("The reply reservation is no longer valid.")
            if self.reservations.get(conversation_id) is not reservation or conversation_id in self.jobs:
                raise ReplyBusy("The reply reservation is no longer valid.")
            reservation.consumed = True
            del self.reservations[conversation_id]
            job = ReplyJob(conversation_id, environment_id)
            self.jobs[conversation_id] = job
            work = job.run(events)
            try:
                job.task = self.work.start(work, name=f"reply-{conversation_id}")
            except BaseException:
                work.close()
                del self.jobs[conversation_id]
                reservation.consumed = False
                self.reservations[conversation_id] = reservation
                raise
            job.task.add_done_callback(lambda _task, owner=job: self._remove_job(conversation_id, owner))
            return job

    def _remove_job(self, conversation_id: int, owner: ReplyJob) -> None:
        with self._lock:
            if self.jobs.get(conversation_id) is owner:
                del self.jobs[conversation_id]

    def cancel(self, conversation_id: int) -> asyncio.Task[None] | None:
        """Ask the running reply to stop, without waiting for it; must run on the event loop. The reply stops at its
        next wait, as with stop. Returns the reply's task, or None when nothing runs."""
        with self._lock:
            job = self.jobs.get(conversation_id)
        if job is None or job.task is None:
            return None
        self.work.cancel(job.task)
        return job.task

    def cancel_job(self, conversation_id: int, job_id: str) -> asyncio.Task[None] | None:
        """Cancel only the requested reply; a later reply in the same conversation is left alone."""
        with self._lock:
            job = self.jobs.get(conversation_id)
        if job is None or job.id != job_id or job.task is None:
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
        with self._lock:
            conversation_ids = list(self.jobs)
        for conversation_id in conversation_ids:
            await self.stop(conversation_id)
