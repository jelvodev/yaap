"""Pushing the board's branch to the project's remote.

Only the board's own branch is pushed, and only through the transport (`app.git.bundles.push_branch`): the place
bundles `refs/heads/yaap-board` alone, the bundle is checked to hold exactly that branch at the commit asked for, and
the transport pushes that one commit to that one branch, never forced. Nothing here pushes all branches or mirrors.
A push publishes every card, so a public remote, or one YAAP cannot judge, needs the user's confirmation."""

from collections.abc import Callable
from dataclasses import dataclass

from app.board.errors import NoRemote, PublicRemote, PushFailed
from app.git.bundles import push_branch
from app.git.common import GitError, UnsafeRepositoryError
from app.git.remote import auth_config, required_push_url
from app.git.service import Repository, folder_of
from app.git.transport import open_transport
from app.github.client import GitHubClient, GitHubError, repository_name

# Whether a remote URL is public: True, False, or None when it cannot be told.
PublicCheck = Callable[[str, str | None], bool | None]


def github_public(url: str, token: str | None) -> bool | None:
    """Whether the remote is a public GitHub repository; None when it is not on GitHub or GitHub cannot say."""
    name = repository_name(url)
    if name is None or not token:
        return None
    try:
        with GitHubClient(token) as client:
            return not client.repository(name).private
    except GitHubError:
        return None


def may_push(public: bool | None, acknowledge_public: bool) -> bool:
    """A private remote is pushed to; a public or unknown one only when the user confirmed."""
    return public is False or acknowledge_public


@dataclass(frozen=True)
class BoardPusher:
    is_public: PublicCheck = github_public

    def push(self, place: Repository, branch: str, commit: str, token: str | None, acknowledge_public: bool) -> None:
        """Push `commit` to `branch` on the remote; raises NoRemote, PublicRemote or PushFailed."""
        try:
            url = required_push_url(place)
        except UnsafeRepositoryError as error:
            raise PushFailed(str(error)) from error
        except GitError as error:
            raise NoRemote() from error
        if not may_push(self.is_public(url, token), acknowledge_public):
            raise PublicRemote()
        try:
            with open_transport(folder_of(place), place) as transport:
                push_branch(transport, url, auth_config(url, token), branch, branch, commit)
        except GitError as error:
            raise PushFailed(f"The board could not be pushed: {error}") from error
