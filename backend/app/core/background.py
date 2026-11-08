"""Work on the event loop that outlives the request which started it (a goal's next turn, memory suggestions, a
review), and stopping it when YAAP shuts down.

Each piece of work runs in its own anyio cancel scope. Stopping it through that scope, unlike `Task.cancel()`, waits
while the work is in a worker thread (anyio shields `to_thread.run_sync`), so the thread's database session has ended
before the engine is disposed. A thread left running would give its connection back to the disposed engine's pool,
where nothing closes it, and the open connection keeps the database file open (on Windows the data folder then
cannot be removed).
"""

import asyncio
from collections.abc import Coroutine
from typing import Any

import anyio


class BackgroundWork:
    """One kind of background work: the tasks that run, each with the cancel scope it runs in."""

    def __init__(self) -> None:
        self.scopes: dict[asyncio.Task[None], anyio.CancelScope] = {}

    def start(self, work: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
        """Run work by itself on the running event loop, referenced until it ends. On the event loop."""
        scope = anyio.CancelScope()

        async def run() -> None:
            with scope:
                await work

        task = asyncio.get_running_loop().create_task(run())
        self.scopes[task] = scope
        task.add_done_callback(self.forget)
        return task

    def forget(self, task: asyncio.Task[None]) -> None:
        self.scopes.pop(task, None)

    async def stop(self) -> None:
        """Stop the work that runs on this event loop and wait until it has ended, worker threads included. Tests run
        several apps one after another, each on its own loop: another loop's work is not this one's to stop."""
        loop = asyncio.get_running_loop()
        running = {task: scope for task, scope in self.scopes.items() if task.get_loop() is loop}
        for scope in running.values():
            scope.cancel()
        await asyncio.gather(*running, return_exceptions=True)
