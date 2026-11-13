"""Remote access of a repository: the origin URL, fetch, push, pull and clone.

The commands that use the network carry the GitHub token, so they run in YAAP in the repository's transport
(`app.git.transport`), a repository only YAAP writes, never in the work repository that the runner can change.
Commits reach the work repository, and leave it, as bundles (`app.git.bundles`); what is read from or written to the
work tree and the objects goes through the repository's place (`app.git.place`), which may be the runner.
"""

import base64
import shutil
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from app.git.bundles import deliver_branches, push_branch, safe_branch_name
from app.git.common import DEFAULT_BRANCH, REMOTE_URL, GitError, UnsafeRepositoryError, is_plain_https_remote
from app.git.place import GitPlace
from app.git.safety import ConfigEntry
from app.git.service import Repository, folder_of, remote_url, repository_status, run_git
from app.git.transport import (
    CLONE_TIMEOUT,
    branch_tip,
    default_branch,
    open_transport,
    read_delivered,
    refs_digest,
    remote_clone,
    remote_fetch,
)
from app.runner.service import RUNNERS, Runners

GITHUB_HOST = "github.com"
NO_REMOTE = "This repository has no remote yet. Set a GitHub URL first."


def validate_remote_url(url: str) -> str:
    """Only plain https URLs: no credentials in the URL, because git would store them in .git/config."""
    url = url.strip()
    if not is_plain_https_remote(url):
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


def required_remote_url(repository: Repository) -> str:
    """The URL of origin for a fetch, push or pull; an unsafe repository says why instead of "no remote"."""
    try:
        url = run_git(["remote", "get-url", "origin"], repository).stdout.strip()
    except UnsafeRepositoryError:
        raise
    except GitError:
        url = ""
    if not url:
        raise GitError(NO_REMOTE)
    return validate_remote_url(url)


def required_push_url(repository: Repository) -> str:
    """The URL git will use for an origin push, including a configured pushurl."""
    try:
        url = run_git(["remote", "get-url", "--push", "origin"], repository).stdout.strip()
    except UnsafeRepositoryError:
        raise
    except GitError as error:
        raise GitError(NO_REMOTE) from error
    if not url:
        raise GitError(NO_REMOTE)
    return validate_remote_url(url)


def set_remote_url(repository: Repository, url: str) -> None:
    url = validate_remote_url(url)
    if remote_url(repository) is None:
        run_git(["remote", "add", "origin", url], repository)
    else:
        run_git(["remote", "set-url", "origin", url], repository)


def clone_repository(
    url: str, target: Path, github_token: str | None, checkout: GitPlace | None = None, runners: Runners = RUNNERS
) -> None:
    """Clone into the transport in YAAP, then build the work repository at `target` from it. With a `checkout` place
    the files are written by the place (the runner), so YAAP's git never reads a work tree the runner can change."""
    url = validate_remote_url(url)
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists()
    place: Repository = checkout or target
    try:
        with open_transport(target, place, runners, fresh=True) as transport:
            remote_clone(transport, url, auth_config(url, github_token))
            branch = default_branch(transport)
            branch = branch if branch and safe_branch_name(branch) else None
            target.mkdir(parents=True, exist_ok=True)
            transport.run("work_init", ["init", "-b", branch or DEFAULT_BRANCH])
            transport.run("work_init", ["remote", "add", "origin", url])
            deliver_branches(transport)
            # An empty remote has no branch to check out.
            if branch and branch_tip(transport, branch):
                arguments = ["checkout", "--force", "-b", branch, "--track", f"origin/{branch}"]
                transport.run("work_checkout", arguments, timeout=CLONE_TIMEOUT)
    except GitError:
        if not existed:
            shutil.rmtree(target, ignore_errors=True)  # a failed clone leaves no half-made repository behind
        raise


def fetch(repository: Repository, github_token: str | None, runners: Runners = RUNNERS) -> None:
    url = required_remote_url(repository)
    with open_transport(folder_of(repository), repository, runners) as transport:
        remote_fetch(transport, url, auth_config(url, github_token))
        deliver_branches(transport)


def fetch_changes(
    folder: Path,
    place_of: Callable[[], Repository],
    github_token: str | None,
    runners: Runners = RUNNERS,
) -> bool:
    """Fetch into the transport and bring the branches to the work repository only when they changed (or an earlier
    delivery did not finish), so a periodic pass does not wake every runner. The remote is read in YAAP from `folder`
    as before: that read carries no token. Returns whether the work repository was updated."""
    url = required_remote_url(folder)
    with open_transport(folder, folder, runners) as transport:
        before = refs_digest(transport)
        remote_fetch(transport, url, auth_config(url, github_token))
        after = refs_digest(transport)
        delivered = read_delivered(transport)
        if before == after and (delivered is None or delivered == after):
            return False
        deliver_branches(replace(transport, place=place_of()))
        return True


def push(repository: Repository, github_token: str | None, runners: Runners = RUNNERS) -> None:
    url = required_push_url(repository)
    status = repository_status(repository)
    if not status.has_commits:
        raise GitError("There are no commits to push yet.")
    if status.branch is None:
        raise GitError("Switch to a branch before pushing.")
    branch = status.branch
    upstream = status.upstream or ""
    remote_branch = upstream.removeprefix("origin/") if upstream.startswith("origin/") else branch
    if not safe_branch_name(branch) or not safe_branch_name(remote_branch):
        raise GitError("YAAP does not push a branch with this name.")
    sha = run_git(["rev-parse", "--verify", f"refs/heads/{branch}"], repository).stdout.strip()
    with open_transport(folder_of(repository), repository, runners) as transport:
        push_branch(transport, url, auth_config(url, github_token), branch, remote_branch, sha)
        if not status.upstream:
            transport.run("work_set_upstream", ["branch", f"--set-upstream-to=origin/{remote_branch}", branch])


def pull(repository: Repository, github_token: str | None, runners: Runners = RUNNERS) -> None:
    """Fast-forward only: when histories have diverged, nothing changes and the user decides what to do."""
    fetch(repository, github_token, runners)
    try:
        run_git(["merge", "--ff-only", "@{upstream}"], repository, timeout=300)
    except GitError as error:
        if "Not possible to fast-forward" in str(error) or "diverged" in str(error):
            raise GitError(
                "Your local commits and the remote have diverged, so a fast-forward pull is not possible. "
                "Nothing was changed."
            ) from error
        raise
