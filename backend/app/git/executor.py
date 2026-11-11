"""Run work-tree git commands on the machine which owns the work tree."""

import shlex
from collections.abc import Mapping, Sequence

from app.git.safety import git_command, git_variables
from app.targets.base import CommandExecutor
from app.git.service import GitError, GitResult, GIT_TIMEOUT_SECONDS, YAAP_AUTHOR


def command_environment(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Fixed git settings for an executor in a Linux runner, with optional commit identity."""
    identity = {
        "GIT_AUTHOR_NAME": YAAP_AUTHOR[0],
        "GIT_AUTHOR_EMAIL": YAAP_AUTHOR[1],
        "GIT_COMMITTER_NAME": YAAP_AUTHOR[0],
        "GIT_COMMITTER_EMAIL": YAAP_AUTHOR[1],
    }
    return {**git_variables(), "GIT_CONFIG_GLOBAL": "/dev/null", **identity, **(extra or {})}


async def run_worktree_git(
    executor: CommandExecutor,
    arguments: Sequence[str],
    *,
    extra_environment: Mapping[str, str] | None = None,
    config: Sequence[str] = (),
    timeout: float = GIT_TIMEOUT_SECONDS,
    accept_exit_codes: Sequence[int] = (0,),
) -> GitResult:
    """Run git with a clean environment through a target executor, never in YAAP's process."""
    env = {"HOME": "/tmp", "PATH": "/usr/local/bin:/usr/bin:/bin", **command_environment(extra_environment)}
    command = shlex.join(
        ["env", "-i", *(f"{name}={value}" for name, value in env.items()), *git_command(arguments, config)]
    )
    result = await executor.run(command, {}, timeout)
    if result.timed_out:
        operation = arguments[0] if arguments else "command"
        raise GitError(f"git {operation} took longer than {timeout:g} seconds and was stopped.")
    if result.exit_code not in accept_exit_codes:
        raise GitError(f"git {arguments[0] if arguments else 'command'} failed: {result.output.strip()}")
    return GitResult(result.output, "", result.exit_code or 0)
