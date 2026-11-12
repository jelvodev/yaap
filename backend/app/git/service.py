"""Git commands for project and Blueprint repositories.

A repository is a `Path` (git runs in YAAP's own process, after `check_repository`) or a `GitPlace` (app.git.place):
for a folder the runner can write, the work-tree and object commands go to the runner through the place's executor.
Remote access (the commands that need the network) is in remote.py, branches in branches.py.
"""

import ast
import io
import os
import re
import shlex
import subprocess
import tarfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from app.core.subprocess_env import safe_subprocess_env
from app.git.common import (
    DEFAULT_BRANCH,
    GIT_TIMEOUT_SECONDS,
    YAAP_AUTHOR,
    GitError,
    GitResult,
    UnsafeRepositoryError,
)
from app.git.executor import archive_on_executor, run_worktree_git
from app.git.place import GitPlace, run_from_worker
from app.git.safety import ConfigEntry, UnsafeRepository, check_repository, git_command, git_variables
from app.targets.base import CommandExecutor

__all__ = ["DEFAULT_BRANCH", "GIT_TIMEOUT_SECONDS", "YAAP_AUTHOR", "GitError", "GitResult", "UnsafeRepositoryError"]

# A repository: a folder git runs in directly, or a place that may send the commands to the runner.
Repository = GitPlace | Path
# Commands that use the network. They carry the GitHub token, so they only ever run in YAAP.
NETWORK_COMMANDS = frozenset({"fetch", "push", "pull", "clone", "ls-remote", "update"})

IDENTITY = {
    "GIT_AUTHOR_NAME": YAAP_AUTHOR[0],
    "GIT_AUTHOR_EMAIL": YAAP_AUTHOR[1],
    "GIT_COMMITTER_NAME": YAAP_AUTHOR[0],
    "GIT_COMMITTER_EMAIL": YAAP_AUTHOR[1],
}


def folder_of(repository: Repository) -> Path:
    """YAAP's path of the repository."""
    return repository.folder if isinstance(repository, GitPlace) else repository


def executor_of(repository: Repository) -> CommandExecutor | None:
    """The executor that runs git for the repository; None when git runs in YAAP's own process."""
    return repository.executor if isinstance(repository, GitPlace) else None


def git_environment(
    folder: Path | None = None,
    extra: Mapping[str, str] | None = None,
    private_config: Sequence[ConfigEntry] = (),
) -> dict[str, str]:
    """YAAP's identity and git's safe settings (`app.git.safety`), then `extra` (another author)."""
    return safe_subprocess_env({**IDENTITY, **git_variables(folder, private_config), **(extra or {})})


def run_git(
    arguments: Sequence[str],
    repository: Repository,
    extra_environment: Mapping[str, str] | None = None,
    config: Sequence[str] = (),
    timeout: int = GIT_TIMEOUT_SECONDS,
    accept_exit_codes: Sequence[int] = (0,),
    private_config: Sequence[ConfigEntry] = (),
    allow_local_transport: bool = False,
) -> GitResult:
    """Run `git -c <config>... <arguments>` in the repository. In YAAP's process, first check that the repository
    sets nothing git could use to run a program (`check_repository`). On a place with an executor the command runs
    there instead, from a worker thread. `config` entries are passed per command and never stored;
    `private_config` (a credential) goes through the environment, never on the command line."""
    executor = executor_of(repository)
    if executor is None:
        return run_in_yaap(
            arguments,
            folder_of(repository),
            extra_environment,
            config,
            timeout,
            accept_exit_codes,
            private_config,
            allow_local_transport,
        )
    if (arguments and arguments[0] in NETWORK_COMMANDS) or private_config or allow_local_transport:
        raise GitError("Commands that use the network run in YAAP on the repository folder, not in the runner.")
    return run_from_worker(
        partial(
            run_worktree_git,
            executor,
            arguments,
            extra_environment=extra_environment,
            config=config,
            timeout=timeout,
            accept_exit_codes=accept_exit_codes,
        )
    )


def run_in_yaap(
    arguments: Sequence[str],
    cwd: Path,
    extra_environment: Mapping[str, str] | None,
    config: Sequence[str],
    timeout: int,
    accept_exit_codes: Sequence[int],
    private_config: Sequence[ConfigEntry],
    allow_local_transport: bool,
) -> GitResult:
    effective_environment = dict(extra_environment or {})
    if arguments and arguments[0] in NETWORK_COMMANDS:
        effective_environment.update(remote_operation_variables())
        if allow_local_transport and arguments[0] == "clone":
            source = Path(arguments[-2]) if len(arguments) >= 3 else None
            if source is None or not source.is_absolute() or not source.is_dir() or not (source / ".git").exists():
                raise GitError("The local clone exception is only for an existing repository folder.")
            effective_environment["GIT_ALLOW_PROTOCOL"] = "https:file"
        elif allow_local_transport:
            raise GitError("The local transport exception is only available for cloning a trusted repository folder.")
    try:
        check_repository(cwd)
    except UnsafeRepository as error:
        raise UnsafeRepositoryError(str(error)) from error
    try:
        completed = subprocess.run(
            git_command(arguments, config),
            cwd=cwd,
            env=git_environment(cwd, effective_environment, private_config),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as error:
        raise GitError("git is not installed.") from error
    except subprocess.TimeoutExpired as error:
        raise GitError(f"git {arguments[0]} took longer than {timeout} seconds and was stopped.") from error
    if completed.returncode not in accept_exit_codes:
        detail = (completed.stderr or completed.stdout).strip()
        raise GitError(f"git {arguments[0]} failed: {detail}")
    result = GitResult(completed.stdout, completed.stderr, completed.returncode)
    try:
        check_repository(cwd)
    except UnsafeRepository as error:
        raise UnsafeRepositoryError(str(error)) from error
    return result


def remote_operation_variables() -> dict[str, str]:
    """Keep network git transports on HTTPS, regardless of a repository's remote URL."""
    return {"GIT_ALLOW_PROTOCOL": "https"}


def archive(repository: Repository, commit: str, paths: Sequence[str] = ()) -> bytes:
    """The files of a commit (only `paths` when given) as a tar archive, read from the object store: nothing is
    checked out, so the repository's branch and work tree stay as they are."""
    executor = executor_of(repository)
    if executor is not None:
        return run_from_worker(archive_on_executor, executor, commit, list(paths))
    folder = folder_of(repository)
    try:
        check_repository(folder)
    except UnsafeRepository as error:
        raise UnsafeRepositoryError(str(error)) from error
    arguments = ["archive", "--format=tar", commit, "--", *paths]
    try:
        completed = subprocess.run(
            git_command(arguments),
            cwd=folder,
            env=git_environment(folder),
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as error:
        raise GitError("git is not installed.") from error
    except subprocess.TimeoutExpired as error:
        raise GitError(f"git archive took longer than {GIT_TIMEOUT_SECONDS} seconds and was stopped.") from error
    if completed.returncode != 0:
        raise GitError(f"git archive failed: {completed.stderr.decode('utf-8', errors='replace').strip()}")
    return completed.stdout


def extract_archive(data: bytes, folder: Path) -> None:
    """Unpack a tar archive from git into folder. The data filter refuses absolute paths, links out of the folder and
    special files."""
    folder.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as bundle:
        bundle.extractall(folder, filter="data")


def init_repository(repository: Repository) -> None:
    """Make the folder a repository when it is none yet; the init runs in the repository's place."""
    folder = folder_of(repository)
    folder.mkdir(parents=True, exist_ok=True)
    if not (folder / ".git").exists():
        run_git(["init", "-b", DEFAULT_BRANCH], repository)


def remote_url(repository: Repository) -> str | None:
    """The URL of origin, or None without one (also for a repository YAAP does not run git in, so lists still show)."""
    try:
        return run_git(["remote", "get-url", "origin"], repository).stdout.strip() or None
    except GitError:
        return None


# --- Status, diff, commit, push, pull ------------------------------------------------------------------------------


@dataclass(frozen=True)
class FileChange:
    path: str
    # Short state: added, modified, deleted, renamed, untracked, conflict.
    state: str
    staged: bool
    # The path before a rename; None for any other change.
    original_path: str | None = None


@dataclass(frozen=True)
class RepositoryStatus:
    branch: str | None
    upstream: str | None
    ahead: int
    behind: int
    has_commits: bool
    remote_url: str | None
    changes: list[FileChange]


STATE_BY_CODE = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed", "C": "copied", "T": "modified"}
QUOTEPATH_OFF = ("core.quotepath=false",)
# Diff output exactly as stored: no colour, no external diff program and no textconv command, whatever is configured.
PLAIN_DIFF = ("--no-color", "--no-ext-diff", "--no-textconv")


def change_state(code: str) -> str:
    return next((STATE_BY_CODE[letter] for letter in code if letter in STATE_BY_CODE), "modified")


def repository_status(repository: Repository) -> RepositoryStatus:
    arguments = ["status", "--porcelain=v2", "--branch", "--untracked-files=all"]
    output = run_git(arguments, repository, config=QUOTEPATH_OFF)
    status = parse_repository_status(output.stdout, remote_url(repository))
    if not status.has_commits and status.upstream:
        # A branch without commits gets no ahead/behind from git; everything on the upstream is "behind".
        try:
            behind = int(run_git(["rev-list", "--count", status.upstream], repository).stdout.strip() or 0)
        except GitError:
            behind = 0
        return RepositoryStatus(
            status.branch,
            status.upstream,
            status.ahead,
            behind,
            status.has_commits,
            status.remote_url,
            status.changes,
        )
    return status


def parse_repository_status(output: str, url: str | None = None, unborn_behind: int = 0) -> RepositoryStatus:
    """Parse `git status --porcelain=v2 --branch` output shared by local and runner git."""
    branch = upstream = None
    ahead = behind = 0
    has_commits = True
    changes: list[FileChange] = []
    for line in output.splitlines():
        if line.startswith("# branch.oid"):
            has_commits = line.split(" ", 2)[2] != "(initial)"
        elif line.startswith("# branch.head"):
            head = line.split(" ", 2)[2]
            branch = None if head == "(detached)" else head
        elif line.startswith("# branch.upstream"):
            upstream = line.split(" ", 2)[2]
        elif line.startswith("# branch.ab"):
            _, _, ahead_text, behind_text = line.split(" ")
            ahead, behind = int(ahead_text), abs(int(behind_text))
        elif line.startswith("1 "):
            fields = line.split(" ", 8)
            changes.append(
                FileChange(unquote_git_path(fields[8]), change_state(fields[1].replace(".", "")), fields[1][0] != ".")
            )
        elif line.startswith("2 "):
            fields = line.split(" ", 9)
            path, _, original = fields[9].partition("\t")
            changes.append(
                FileChange(
                    unquote_git_path(path),
                    "renamed",
                    fields[1][0] != ".",
                    unquote_git_path(original) or None,
                )
            )
        elif line.startswith("u "):
            changes.append(FileChange(unquote_git_path(line.split(" ", 10)[10]), "conflict", False))
        elif line.startswith("? "):
            changes.append(FileChange(unquote_git_path(line[2:]), "untracked", False))
    if not has_commits:
        behind = unborn_behind
    return RepositoryStatus(branch, upstream, ahead, behind, has_commits, url, changes)


def unquote_git_path(path: str) -> str:
    """Decode the quoted pathname Git uses for control characters in porcelain output."""
    if not path.startswith('"'):
        return path
    try:
        decoded = ast.literal_eval(path)
    except (SyntaxError, ValueError):
        return path
    return decoded if isinstance(decoded, str) else path


MAX_DIFF_CHARS = 400_000


MAX_NEW_FILE_BYTES = 200_000


def new_file_size(repository: Repository, path: str) -> int:
    """The size of an untracked file, or more than the limit for one that is not a plain file. On a place with an
    executor the executor measures it, without following a link."""
    too_big = MAX_NEW_FILE_BYTES + 1
    executor = executor_of(repository)
    if executor is None:
        file = folder_of(repository) / path
        return file.stat().st_size if file.is_file() else too_big
    quoted = shlex.quote(path)
    command = f"if [ ! -L {quoted} ] && [ -f {quoted} ]; then wc -c < {quoted}; else echo 0; fi"
    stat = run_from_worker(executor.capture, command, 10)
    try:
        return int(stat.stdout.decode("utf-8", errors="replace").strip()) if stat.exit_code == 0 else too_big
    except ValueError:
        return too_big


def repository_diff(repository: Repository) -> str:
    """Everything that a commit of all changes would contain, including new files. Read-only: the index is untouched."""
    status = repository_status(repository)
    null_device = os.devnull if executor_of(repository) is None else "/dev/null"
    parts: list[str] = []
    if status.has_commits:
        parts.append(run_git(["diff", "HEAD", *PLAIN_DIFF], repository, config=QUOTEPATH_OFF).stdout)
    else:
        parts.append(run_git(["diff", "--cached", *PLAIN_DIFF], repository, config=QUOTEPATH_OFF).stdout)
    for change in status.changes:
        if change.state != "untracked":
            continue
        if new_file_size(repository, change.path) <= MAX_NEW_FILE_BYTES:
            # Exit code 1 means "there are differences", which is always the case for a new file.
            parts.append(
                run_git(
                    ["diff", "--no-index", *PLAIN_DIFF, "--", null_device, change.path],
                    repository,
                    config=QUOTEPATH_OFF,
                    accept_exit_codes=(0, 1),
                ).stdout
            )
        else:
            parts.append(f"New file {change.path} (too large to show)\n")
    diff = "".join(parts)
    if len(diff) > MAX_DIFF_CHARS:
        return diff[:MAX_DIFF_CHARS] + "\n\n[The diff is longer than this; the rest is not shown.]"
    return diff


@dataclass(frozen=True)
class CommitEntry:
    hash: str
    short_hash: str
    author: str
    date: str
    subject: str


# Fields of `git log`, split by the ASCII unit separator, which never appears in them.
LOG_FORMAT = "%x1f".join(["%H", "%h", "%an", "%aI", "%s"])
COMMIT_HASH = re.compile(r"[0-9a-f]{7,40}")
MAX_LOG_ENTRIES = 200


def repository_log(repository: Repository, path: str | None = None, limit: int = 50) -> list[CommitEntry]:
    """The most recent commits, newest first; with a path, only those that touched it (following renames)."""
    if not repository_status(repository).has_commits:
        return []
    arguments = ["log", f"--max-count={max(1, min(limit, MAX_LOG_ENTRIES))}", f"--format={LOG_FORMAT}"]
    if path:
        arguments += ["--follow", "--", path]
    output = run_git(arguments, repository, config=QUOTEPATH_OFF).stdout
    entries = []
    for line in output.splitlines():
        fields = line.split("\x1f")
        if len(fields) == 5:
            entries.append(CommitEntry(*fields))
    return entries


def commit_diff(repository: Repository, commit: str, path: str | None = None) -> str:
    """What one commit changed, optionally limited to one path."""
    if not COMMIT_HASH.fullmatch(commit):
        raise GitError("That is not a commit hash.")
    arguments = ["show", *PLAIN_DIFF, "--format=", commit]
    if path:
        arguments += ["--", path]
    diff = run_git(arguments, repository, config=QUOTEPATH_OFF).stdout
    if len(diff) > MAX_DIFF_CHARS:
        return diff[:MAX_DIFF_CHARS] + "\n\n[The diff is longer than this; the rest is not shown.]"
    return diff


def commit_all(repository: Repository, message: str, author: tuple[str, str] | None = None) -> str:
    message = message.strip()
    if not message:
        raise GitError("A commit needs a message.")
    run_git(["add", "--all"], repository)
    if not repository_status(repository).changes:
        raise GitError("There is nothing to commit.")
    extra = {}
    if author:
        extra = {"GIT_AUTHOR_NAME": author[0], "GIT_AUTHOR_EMAIL": author[1]}
    run_git(["commit", "-m", message], repository, extra)
    return run_git(["rev-parse", "--short", "HEAD"], repository).stdout.strip()
