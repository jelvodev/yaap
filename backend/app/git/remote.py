"""Remote access of a repository: the origin URL, fetch, push, pull and clone.

The commands that use the network run in YAAP, on the folder YAAP sees, because they carry the GitHub token and the
runner must never have it. What they read from or write to the work tree and the objects goes through the
repository's place (`app.git.place`), which may be the runner.
"""

import base64
import re
from pathlib import Path

from app.git.common import GitError, UnsafeRepositoryError
from app.git.place import GitPlace
from app.git.safety import RECEIVE_PACK, UPLOAD_PACK, ConfigEntry
from app.git.service import Repository, folder_of, remote_url, repository_status, run_git

GITHUB_HOST = "github.com"
REMOTE_URL = re.compile(r"^https://(?P<host>[A-Za-z0-9.-]+)(?::\d+)?/[^\s@]+$")
NO_REMOTE = "This repository has no remote yet. Set a GitHub URL first."


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


def clone_repository(url: str, target: Path, github_token: str | None, checkout: GitPlace | None = None) -> None:
    """Clone in YAAP. With a `checkout` place the clone has no work tree yet: the files are written by the place
    (the runner), so YAAP's git never reads a work tree the runner can change."""
    url = validate_remote_url(url)
    target.parent.mkdir(parents=True, exist_ok=True)
    authorization = auth_config(url, github_token)
    arguments = ["clone", UPLOAD_PACK, *(["--no-checkout"] if checkout else []), url, str(target)]
    run_git(arguments, target.parent, private_config=authorization, timeout=600)
    if checkout is None:
        return
    # An empty remote has no commit to check out.
    if run_git(["rev-parse", "--verify", "--quiet", "HEAD"], checkout, accept_exit_codes=(0, 1)).exit_code != 0:
        return
    branch = run_git(["symbolic-ref", "--short", "HEAD"], checkout).stdout.strip()
    run_git(["checkout", "--force", branch], checkout, timeout=600)


def fetch(repository: Repository, github_token: str | None) -> None:
    url = required_remote_url(repository)
    authorization = auth_config(url, github_token)
    folder = folder_of(repository)
    run_git(["fetch", UPLOAD_PACK, "--prune", "origin"], folder, private_config=authorization, timeout=300)


def push(repository: Repository, github_token: str | None) -> None:
    url = required_push_url(repository)
    status = repository_status(repository)
    if not status.has_commits:
        raise GitError("There are no commits to push yet.")
    arguments = ["push", RECEIVE_PACK] + ([] if status.upstream else ["--set-upstream", "origin", "HEAD"])
    run_git(arguments, folder_of(repository), private_config=auth_config(url, github_token), timeout=300)


def pull(repository: Repository, github_token: str | None) -> None:
    """Fast-forward only: when histories have diverged, nothing changes and the user decides what to do."""
    url = required_remote_url(repository)
    authorization = auth_config(url, github_token)
    run_git(["fetch", UPLOAD_PACK, "origin"], folder_of(repository), private_config=authorization, timeout=300)
    try:
        run_git(["merge", "--ff-only", "@{upstream}"], repository, timeout=300)
    except GitError as error:
        if "Not possible to fast-forward" in str(error) or "diverged" in str(error):
            raise GitError(
                "Your local commits and the remote have diverged, so a fast-forward pull is not possible. "
                "Nothing was changed."
            ) from error
        raise
