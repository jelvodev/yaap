"""Run work-tree git commands on the machine which owns the work tree."""

import shlex
from collections.abc import Mapping, Sequence

from app.git.common import GIT_TIMEOUT_SECONDS, YAAP_AUTHOR, GitError, GitResult
from app.git.safety import git_command, git_variables
from app.targets.base import CapturedResult, CommandExecutor


def command_environment(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Fixed git settings for an executor in a Linux runner, with optional commit identity."""
    identity = {
        "GIT_AUTHOR_NAME": YAAP_AUTHOR[0],
        "GIT_AUTHOR_EMAIL": YAAP_AUTHOR[1],
        "GIT_COMMITTER_NAME": YAAP_AUTHOR[0],
        "GIT_COMMITTER_EMAIL": YAAP_AUTHOR[1],
    }
    return {**git_variables(), "GIT_CONFIG_GLOBAL": "/dev/null", **identity, **(extra or {})}


async def capture_git(
    executor: CommandExecutor,
    arguments: Sequence[str],
    *,
    extra_environment: Mapping[str, str] | None = None,
    config: Sequence[str] = (),
    timeout: float = GIT_TIMEOUT_SECONDS,
) -> CapturedResult:
    """Run git with a clean environment through a target executor, never in YAAP's process; a timeout is an error."""
    env = {"HOME": "/tmp", "PATH": "/usr/local/bin:/usr/bin:/bin", **command_environment(extra_environment)}
    command = shlex.join(
        ["env", "-i", *(f"{name}={value}" for name, value in env.items()), *git_command(arguments, config)]
    )
    result = await executor.capture(command, timeout)
    if result.timed_out:
        operation = arguments[0] if arguments else "command"
        raise GitError(f"git {operation} took longer than {timeout:g} seconds and was stopped.")
    return result


def decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


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
    result = await capture_git(
        executor, arguments, extra_environment=extra_environment, config=config, timeout=timeout
    )
    stdout, stderr = decode(result.stdout), decode(result.stderr)
    if result.exit_code not in accept_exit_codes:
        detail = (stderr or stdout).strip()
        raise GitError(f"git {arguments[0] if arguments else 'command'} failed: {detail}")
    return GitResult(stdout, stderr, result.exit_code or 0)


async def archive_on_executor(executor: CommandExecutor, commit: str, paths: Sequence[str]) -> bytes:
    """The files of a commit as the raw bytes of a tar archive. The executor keeps standard output as bytes, so the
    archive needs no encoding (such as base64) on its way from the machine which owns the repository."""
    result = await capture_git(executor, ["archive", "--format=tar", commit, "--", *paths])
    if result.exit_code != 0:
        raise GitError(f"git archive failed: {decode(result.stderr).strip()}")
    return result.stdout
