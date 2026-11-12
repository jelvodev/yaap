"""Where git runs for a folder: in YAAP's own process, or in the runner of the environment that owns the folder.

A project folder in the workspace can be written by processes in the runner, so git must not read its configuration
and objects in YAAP, where a filter or hook it finds would run with YAAP's rights. Work-tree and object commands
there go to the runner; the commands that need the network (and so the GitHub token) stay in YAAP, on the folder
YAAP sees (`GitPlace.folder`). When the runner cannot be used, the command fails: it never falls back to YAAP.
"""

import asyncio
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeVar

import anyio.from_thread

from app.git.common import GitError
from app.runner.service import RUNNERS, Runners
from app.targets.base import CommandExecutor, TargetError

GitPlaceKind = Literal["runner", "yaap"]
Result = TypeVar("Result")


@dataclass(frozen=True)
class GitPlace:
    """A repository folder and the executor that runs its work-tree and object git commands."""

    # YAAP's path of the repository: for local file checks and for the network commands, which run in YAAP.
    folder: Path
    # None: git runs in YAAP's own process.
    executor: CommandExecutor | None = None
    # The environment's folder in YAAP's workspace, which the runner mounts: where commits are handed to the runner.
    environment_dir: Path | None = None


def git_place_kind(folder: Path, workspace_dir: Path | None, runner_enabled: bool) -> GitPlaceKind:
    """"runner" only for the workspace itself or a folder inside it, while the runner is on. Inside is checked both
    as written and with links followed: a project folder the runner replaced by a link to somewhere else in YAAP
    still counts as the workspace's, so its git goes to the runner (where the link leads nowhere) and fails there."""
    if not runner_enabled or workspace_dir is None:
        return "yaap"
    as_written = Path(os.path.abspath(folder)).is_relative_to(os.path.abspath(workspace_dir))
    followed = folder.resolve().is_relative_to(workspace_dir.resolve())
    return "runner" if as_written or followed else "yaap"


def git_place(environment_id: int, folder: Path, runners: Runners = RUNNERS) -> GitPlace:
    """The place of git for a folder of the environment. Synchronous: call it from a router or a worker thread, with
    no write of the caller's own pending (building and using the runner records its use in sessions of their own).
    Raises GitError when the folder needs the runner and the runner cannot be used."""
    if git_place_kind(folder, runners.workspace_dir, runners.enabled) == "yaap":
        return GitPlace(folder)
    try:
        runtime = runners.git_runtime(environment_id)
        return GitPlace(folder, runtime.executor(str(folder)), runtime.environment_dir)
    except TargetError as error:  # RunnerUnavailable is one; so is a folder outside the environment
        raise GitError(str(error)) from error


def run_from_worker(function: Callable[..., Awaitable[Result]], *arguments: object) -> Result:
    """Run a coroutine function on YAAP's event loop from a worker thread and wait for its result.

    Sync services run in worker threads (sync routers, `anyio.to_thread.run_sync`); the executor is async and
    belongs to the loop. On the loop's own thread this would block it, so that is refused."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return anyio.from_thread.run(function, *arguments)
    raise RuntimeError("git on a runner blocks the thread it runs on; call it from a worker thread.")
