"""Git commands for project and Blueprint repositories, run with the git binary in the container."""

import base64
import io
import os
import re
import subprocess
import tarfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from app.core.subprocess_env import safe_subprocess_env
from app.git.safety import (
    RECEIVE_PACK,
    UPLOAD_PACK,
    ConfigEntry,
    UnsafeRepository,
    check_repository,
    git_command,
    git_variables,
)

GIT_TIMEOUT_SECONDS = 120
DEFAULT_BRANCH = "main"
# Commits made by YAAP itself (for example when the Blueprint editor saves a file).
YAAP_AUTHOR = ("YAAP", "yaap@localhost")


class GitError(RuntimeError):
    """A git command failed; the message is safe to show to the user."""


class UnsafeRepositoryError(GitError):
    """YAAP does not run git in this repository: its configuration names something git could run."""


@dataclass(frozen=True)
class GitResult:
    stdout: str
    stderr: str


IDENTITY = {
    "GIT_AUTHOR_NAME": YAAP_AUTHOR[0],
    "GIT_AUTHOR_EMAIL": YAAP_AUTHOR[1],
    "GIT_COMMITTER_NAME": YAAP_AUTHOR[0],
    "GIT_COMMITTER_EMAIL": YAAP_AUTHOR[1],
}


def git_environment(
    folder: Path | None = None,
    extra: Mapping[str, str] | None = None,
    private_config: Sequence[ConfigEntry] = (),
) -> dict[str, str]:
    """YAAP's identity and git's safe settings (`app.git.safety`), then `extra` (another author)."""
    return safe_subprocess_env({**IDENTITY, **git_variables(folder, private_config), **(extra or {})})


def run_git(
    arguments: Sequence[str],
    cwd: Path,
    extra_environment: Mapping[str, str] | None = None,
    config: Sequence[str] = (),
    timeout: int = GIT_TIMEOUT_SECONDS,
    accept_exit_codes: Sequence[int] = (0,),
    private_config: Sequence[ConfigEntry] = (),
) -> GitResult:
    """Run `git -c <config>... <arguments>` in cwd, after checking that the repository there sets nothing git could
    use to run a program (`check_repository`). `config` entries are passed per command and never stored;
    `private_config` (a credential) goes through the environment, never on the command line."""
    try:
        check_repository(cwd)
    except UnsafeRepository as error:
        raise UnsafeRepositoryError(str(error)) from error
    try:
        completed = subprocess.run(
            git_command(arguments, config),
            cwd=cwd,
            env=git_environment(cwd, extra_environment, private_config),
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
    return GitResult(completed.stdout, completed.stderr)


def archive(repository: Path, commit: str, paths: Sequence[str] = ()) -> bytes:
    """The files of a commit (only `paths` when given) as a tar archive, read from the object store: nothing is
    checked out, so the repository's branch and work tree stay as they are."""
    try:
        check_repository(repository)
    except UnsafeRepository as error:
        raise UnsafeRepositoryError(str(error)) from error
    arguments = ["archive", "--format=tar", commit, "--", *paths]
    try:
        completed = subprocess.run(
            git_command(arguments),
            cwd=repository,
            env=git_environment(repository),
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


def init_repository(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    if not (folder / ".git").exists():
        run_git(["init", "-b", DEFAULT_BRANCH], folder)


# --- Remote access -------------------------------------------------------------------------------------------------

GITHUB_HOST = "github.com"
REMOTE_URL = re.compile(r"^https://(?P<host>[A-Za-z0-9.-]+)(?::\d+)?/[^\s@]+$")


def validate_remote_url(url: str) -> str:
    """Only plain https URLs: no credentials in the URL, because git would store them in .git/config."""
    url = url.strip()
    if not REMOTE_URL.match(url):
        raise GitError(
            "Use an https URL without a user name or token in it, for example https://github.com/owner/repository.git."
        )
    return url


def auth_config(remote_url: str | None, github_token: str | None) -> tuple[ConfigEntry, ...]:
    """The GitHub token for this one command, as a setting passed through git's environment (never on the command
    line or on disk), scoped to github.com; nothing for another host."""
    if not github_token or not remote_url:
        return ()
    match = REMOTE_URL.match(remote_url)
    if not match or match.group("host").lower() != GITHUB_HOST:
        return ()
    basic = base64.b64encode(f"x-access-token:{github_token}".encode()).decode()
    return ((f"http.https://{GITHUB_HOST}/.extraheader", f"AUTHORIZATION: basic {basic}"),)


def remote_url(folder: Path) -> str | None:
    """The URL of origin, or None without one (also for a repository YAAP does not run git in, so lists still show)."""
    try:
        return run_git(["remote", "get-url", "origin"], folder).stdout.strip() or None
    except GitError:
        return None


def required_remote_url(folder: Path) -> str:
    """The URL of origin for a fetch, push or pull; an unsafe repository says why instead of "no remote"."""
    try:
        url = run_git(["remote", "get-url", "origin"], folder).stdout.strip()
    except UnsafeRepositoryError:
        raise
    except GitError:
        url = ""
    if not url:
        raise GitError("This repository has no remote yet. Set a GitHub URL first.")
    return url


def set_remote_url(folder: Path, url: str) -> None:
    url = validate_remote_url(url)
    if remote_url(folder) is None:
        run_git(["remote", "add", "origin", url], folder)
    else:
        run_git(["remote", "set-url", "origin", url], folder)


def clone_repository(url: str, target: Path, github_token: str | None) -> None:
    url = validate_remote_url(url)
    target.parent.mkdir(parents=True, exist_ok=True)
    authorization = auth_config(url, github_token)
    run_git(["clone", UPLOAD_PACK, url, str(target)], target.parent, private_config=authorization, timeout=600)


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


def repository_status(folder: Path) -> RepositoryStatus:
    output = run_git(["status", "--porcelain=v2", "--branch", "--untracked-files=all"], folder, config=QUOTEPATH_OFF)
    branch = upstream = None
    ahead = behind = 0
    has_commits = True
    changes: list[FileChange] = []
    for line in output.stdout.splitlines():
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
            changes.append(FileChange(fields[8], change_state(fields[1].replace(".", "")), fields[1][0] != "."))
        elif line.startswith("2 "):
            fields = line.split(" ", 9)
            path, _, original = fields[9].partition("\t")
            changes.append(FileChange(path, "renamed", fields[1][0] != ".", original or None))
        elif line.startswith("u "):
            changes.append(FileChange(line.split(" ", 10)[10], "conflict", False))
        elif line.startswith("? "):
            changes.append(FileChange(line[2:], "untracked", False))
    if not has_commits and upstream:
        # A branch without commits gets no ahead/behind from git; everything on the upstream is "behind".
        try:
            behind = int(run_git(["rev-list", "--count", upstream], folder).stdout.strip() or 0)
        except GitError:
            behind = 0
    return RepositoryStatus(branch, upstream, ahead, behind, has_commits, remote_url(folder), changes)


MAX_DIFF_CHARS = 400_000


MAX_NEW_FILE_BYTES = 200_000


def repository_diff(folder: Path) -> str:
    """Everything that a commit of all changes would contain, including new files. Read-only: the index is untouched."""
    status = repository_status(folder)
    parts: list[str] = []
    if status.has_commits:
        parts.append(run_git(["diff", "HEAD", *PLAIN_DIFF], folder, config=QUOTEPATH_OFF).stdout)
    else:
        parts.append(run_git(["diff", "--cached", *PLAIN_DIFF], folder, config=QUOTEPATH_OFF).stdout)
    for change in status.changes:
        if change.state != "untracked":
            continue
        file = folder / change.path
        if file.is_file() and file.stat().st_size <= MAX_NEW_FILE_BYTES:
            # Exit code 1 means "there are differences", which is always the case for a new file.
            parts.append(
                run_git(
                    ["diff", "--no-index", *PLAIN_DIFF, "--", os.devnull, change.path],
                    folder,
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


def repository_log(folder: Path, path: str | None = None, limit: int = 50) -> list[CommitEntry]:
    """The most recent commits, newest first; with a path, only those that touched it (following renames)."""
    if not repository_status(folder).has_commits:
        return []
    arguments = ["log", f"--max-count={max(1, min(limit, MAX_LOG_ENTRIES))}", f"--format={LOG_FORMAT}"]
    if path:
        arguments += ["--follow", "--", path]
    output = run_git(arguments, folder, config=QUOTEPATH_OFF).stdout
    entries = []
    for line in output.splitlines():
        fields = line.split("\x1f")
        if len(fields) == 5:
            entries.append(CommitEntry(*fields))
    return entries


def commit_diff(folder: Path, commit: str, path: str | None = None) -> str:
    """What one commit changed, optionally limited to one path."""
    if not COMMIT_HASH.fullmatch(commit):
        raise GitError("That is not a commit hash.")
    arguments = ["show", *PLAIN_DIFF, "--format=", commit]
    if path:
        arguments += ["--", path]
    diff = run_git(arguments, folder, config=QUOTEPATH_OFF).stdout
    if len(diff) > MAX_DIFF_CHARS:
        return diff[:MAX_DIFF_CHARS] + "\n\n[The diff is longer than this; the rest is not shown.]"
    return diff


def commit_all(folder: Path, message: str, author: tuple[str, str] | None = None) -> str:
    message = message.strip()
    if not message:
        raise GitError("A commit needs a message.")
    run_git(["add", "--all"], folder)
    if not repository_status(folder).changes:
        raise GitError("There is nothing to commit.")
    extra = {}
    if author:
        extra = {"GIT_AUTHOR_NAME": author[0], "GIT_AUTHOR_EMAIL": author[1]}
    run_git(["commit", "-m", message], folder, extra)
    return run_git(["rev-parse", "--short", "HEAD"], folder).stdout.strip()


def fetch(folder: Path, github_token: str | None) -> None:
    url = required_remote_url(folder)
    authorization = auth_config(url, github_token)
    run_git(["fetch", UPLOAD_PACK, "--prune", "origin"], folder, private_config=authorization, timeout=300)


def push(folder: Path, github_token: str | None) -> None:
    url = required_remote_url(folder)
    status = repository_status(folder)
    if not status.has_commits:
        raise GitError("There are no commits to push yet.")
    arguments = ["push", RECEIVE_PACK] + ([] if status.upstream else ["--set-upstream", "origin", "HEAD"])
    run_git(arguments, folder, private_config=auth_config(url, github_token), timeout=300)


def pull(folder: Path, github_token: str | None) -> None:
    """Fast-forward only: when histories have diverged, nothing changes and the user decides what to do."""
    url = required_remote_url(folder)
    try:
        run_git(["pull", UPLOAD_PACK, "--ff-only"], folder, private_config=auth_config(url, github_token), timeout=300)
    except GitError as error:
        if "Not possible to fast-forward" in str(error) or "diverged" in str(error):
            raise GitError(
                "Your local commits and the remote have diverged, so a fast-forward pull is not possible. "
                "Nothing was changed."
            ) from error
        raise


# --- Branches ----------------------------------------------------------------------------------


@dataclass
class Branch:
    name: str
    current: bool
    upstream: str | None
    # "ahead 2, behind 1" as git reports it; empty when even or without upstream.
    track: str
    subject: str


def check_branch_name(folder: Path, name: str) -> str:
    name = name.strip()
    if not name or name.startswith("-"):
        raise GitError("Name the branch, such as feature/login.")
    try:
        run_git(["check-ref-format", "--branch", name], folder)
    except UnsafeRepositoryError:
        raise
    except GitError as error:
        raise GitError(f"{name} is not a valid branch name.") from error
    return name


def list_branches(folder: Path) -> list[Branch]:
    output = run_git(
        ["for-each-ref", "--sort=-committerdate", "--format=%(HEAD)%00%(refname:short)%00%(upstream:short)%00%(upstream:track,nobracket)%00%(contents:subject)", "refs/heads"],
        folder,
    )
    branches = []
    for line in output.stdout.splitlines():
        head, name, upstream, track, subject = (line.split("\0") + [""] * 5)[:5]
        branches.append(Branch(name, head == "*", upstream or None, track, subject))
    return branches


def create_branch(folder: Path, name: str, start: str | None = None, switch: bool = True) -> None:
    name = check_branch_name(folder, name)
    arguments = ["switch", "-c", name] if switch else ["branch", name]
    if start:
        arguments.append(start)
    run_git(arguments, folder)


def switch_branch(folder: Path, name: str) -> None:
    """Switch to a local branch, or to a new local branch tracking origin/<name>. Changes that would be lost stop it."""
    run_git(["switch", check_branch_name(folder, name)], folder)


def delete_branch(folder: Path, name: str, force: bool = False) -> None:
    name = check_branch_name(folder, name)
    status = repository_status(folder)
    if status.branch == name:
        raise GitError("This is the current branch. Switch to another branch first.")
    try:
        run_git(["branch", "-D" if force else "-d", name], folder)
    except GitError as error:
        if "not fully merged" in str(error):
            raise GitError(f"{name} has commits that are not merged. Delete it anyway to lose them.") from error
        raise


def branch_summary(folder: Path, base: str, limit: int = 60) -> tuple[str, str]:
    """The commits on HEAD since base, and the changed files with their size of change, for a pull request text."""
    for candidate in (f"origin/{base}", base):
        try:
            log = run_git(["log", "--format=- %s%n%b", f"-{limit}", f"{candidate}..HEAD"], folder).stdout
            stat = run_git(["diff", "--stat", *PLAIN_DIFF, f"{candidate}...HEAD"], folder).stdout
            return log.strip(), stat.strip()
        except UnsafeRepositoryError:
            raise
        except GitError:
            continue
    raise GitError(f"The base branch {base} is not known here. Fetch first.")
