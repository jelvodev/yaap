"""Work on the event loop that outlives the request which started it (a goal's next turn, memory suggestions, a
review, a summary, a telemetry export), and stopping it when YAAP shuts down.

Each piece of work runs in its own anyio cancel scope. Stopping it through that scope, unlike `Task.cancel()`, waits
while the work is in a worker thread (anyio shields `to_thread.run_sync`), so the thread's database session has ended
before the engine is disposed. A thread left running would give its connection back to the disposed engine's pool,
where nothing closes it, and the open connection keeps the database file open (on Windows the data folder then
cannot be removed).
"""

import asyncio
from collections.abc import Coroutine
from typing import Any, Generic, Protocol, TypeVar

import anyio

T = TypeVar("T")


class Stoppable(Protocol):
    """Work that shutdown stops and waits for."""

    async def stop(self) -> None: ...


class BackgroundWork(Generic[T]):
    """One kind of background work: the tasks that run, each with the cancel scope it runs in. A task's result is
    its work's result, or None when the work was stopped."""

    def __init__(self) -> None:
        self.scopes: dict[asyncio.Task[T | None], anyio.CancelScope] = {}

    def start(self, work: Coroutine[Any, Any, T], name: str | None = None) -> asyncio.Task[T | None]:
        """Run work by itself on the running event loop, referenced until it ends. On the event loop."""
        scope = anyio.CancelScope()

        async def run() -> T | None:
            with scope:
                return await work
            return None

        task = asyncio.get_running_loop().create_task(run(), name=name)
        self.scopes[task] = scope
        task.add_done_callback(self.forget)
        return task

    def forget(self, task: asyncio.Task[T | None]) -> None:
        self.scopes.pop(task, None)

    def running(self) -> list[asyncio.Task[T | None]]:
        """The tasks that run on this event loop. Tests run several apps one after another, each on its own loop:
        another loop's work is not this one's."""
        loop = asyncio.get_running_loop()
        return [task for task in self.scopes if task.get_loop() is loop]

    def cancel(self, task: asyncio.Task[T | None]) -> None:
        """Stop one task through its scope; awaiting the task then waits for a worker thread it is in."""
        scope = self.scopes.get(task)
        if scope is not None:
            scope.cancel()

    async def wait(self, task: asyncio.Task[T | None]) -> None:
        """Until the task has ended. asyncio.wait rather than await: a waiter that is cancelled itself must not cancel
        the task, whose cleanup would then be cut short."""
        await asyncio.wait({task})

    async def stop(self) -> None:
        """Stop the work that runs on this event loop and wait until it has ended, worker threads included."""
        running = self.running()
        for task in running:
            self.cancel(task)
        await asyncio.gather(*running, return_exceptions=True)
