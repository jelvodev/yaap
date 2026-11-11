"""Read and change a git work tree through its owning target executor."""

import shlex
from collections.abc import Mapping
from functools import partial
from pathlib import Path

import anyio

from app.git.executor import run_worktree_git
from app.git.service import (
    MAX_DIFF_CHARS,
    MAX_NEW_FILE_BYTES,
    PLAIN_DIFF,
    QUOTEPATH_OFF,
    Branch,
    GitError,
    RepositoryStatus,
    parse_branches,
    parse_repository_status,
    auth_config,
    remote_operation_variables,
    required_push_url,
    run_git,
)
from app.git.safety import RECEIVE_PACK
from app.targets.base import CommandExecutor


async def repository_status(executor: CommandExecutor) -> RepositoryStatus:
    """Read branch and work-tree status on the target that owns the files."""
    status_output = await run_worktree_git(
        executor, ["status", "--porcelain=v2", "--branch", "--untracked-files=all"], config=QUOTEPATH_OFF
    )
    try:
        remote_output = await run_worktree_git(executor, ["remote", "get-url", "origin"], accept_exit_codes=(0, 2))
        lines = [line for line in remote_output.stdout.splitlines() if line.strip()]
        url = lines[-1].strip() if remote_output.exit_code == 0 and lines else None
    except GitError:
        url = None
    parsed = parse_repository_status(status_output.stdout, url)
    if parsed.has_commits or not parsed.upstream:
        return parsed
    try:
        behind_output = await run_worktree_git(executor, ["rev-list", "--count", parsed.upstream])
        behind = int(next(line for line in reversed(behind_output.stdout.splitlines()) if line.strip()).strip())
    except GitError:
        behind = 0
    return parse_repository_status(status_output.stdout, url, behind)


async def repository_diff(executor: CommandExecutor) -> str:
    """Return the review diff from the owning target, including small untracked files."""
    status = await repository_status(executor)
    parts: list[str] = []
    if status.has_commits:
        parts.append((await run_worktree_git(executor, ["diff", "HEAD", *PLAIN_DIFF], config=QUOTEPATH_OFF)).stdout)
    else:
        parts.append((await run_worktree_git(executor, ["diff", "--cached", *PLAIN_DIFF], config=QUOTEPATH_OFF)).stdout)
    for change in status.changes:
        if change.state != "untracked":
            continue
        # Ask the target shell for a bounded stat without interpreting the file contents.
        quoted = shlex.quote(change.path)
        command = f"if [ ! -L {quoted} ] && [ -f {quoted} ]; then wc -c < {quoted}; else echo 0; fi"
        stat = await executor.run(command, {}, 10)
        try:
            file_size = (
                int(stat.output.strip())
                if stat.exit_code == 0 and not stat.timed_out
                else MAX_NEW_FILE_BYTES + 1
            )
        except ValueError:
            file_size = MAX_NEW_FILE_BYTES + 1
        if file_size <= MAX_NEW_FILE_BYTES:
            diff = await run_worktree_git(
                executor,
                ["diff", "--no-index", *PLAIN_DIFF, "--", "/dev/null", change.path],
                config=QUOTEPATH_OFF,
                accept_exit_codes=(0, 1),
            )
            parts.append(diff.stdout)
        else:
            parts.append(f"New file {change.path} (too large to show)\n")
    diff = "".join(parts)
    if len(diff) > MAX_DIFF_CHARS:
        return diff[:MAX_DIFF_CHARS] + "\n\n[The diff is longer than this; the rest is not shown.]"
    return diff


async def list_branches(executor: CommandExecutor) -> list[Branch]:
    """List local branches without starting git in YAAP."""
    output = await run_worktree_git(
        executor,
        [
            "for-each-ref",
            "--sort=-committerdate",
            "--format=%(HEAD)%00%(refname:short)%00%(upstream:short)%00%(upstream:track,nobracket)%00"
            "%(contents:subject)",
            "refs/heads",
        ],
    )
    return parse_branches(output.stdout)


async def commit_all(executor: CommandExecutor, message: str, author: tuple[str, str] | None = None) -> str:
    """Stage and commit all changed paths on the target where the work tree lives."""
    message = message.strip()
    if not message:
        raise GitError("A commit needs a message.")
    extra: Mapping[str, str] = {}
    if author:
        extra = {"GIT_AUTHOR_NAME": author[0], "GIT_AUTHOR_EMAIL": author[1]}
    await run_worktree_git(executor, ["add", "--all"])
    status = await repository_status(executor)
    if not status.changes:
        raise GitError("There is nothing to commit.")
    await run_worktree_git(executor, ["commit", "-m", message], extra_environment=extra)
    output = await run_worktree_git(executor, ["rev-parse", "--short", "HEAD"])
    return next(line for line in reversed(output.stdout.splitlines()) if line.strip()).strip()


async def push_commits(executor: CommandExecutor, folder: Path, github_token: str | None) -> None:
    """Push after reading branch state on the work-tree owner; network git stays in YAAP."""
    url = required_push_url(folder)
    status = await repository_status(executor)
    if not status.has_commits:
        raise GitError("There are no commits to push yet.")
    arguments = ["push", RECEIVE_PACK] + ([] if status.upstream else ["--set-upstream", "origin", "HEAD"])
    await anyio.to_thread.run_sync(
        partial(
            run_git,
            arguments,
            folder,
            extra_environment=remote_operation_variables(),
            private_config=auth_config(url, github_token),
            timeout=300,
        )
    )
