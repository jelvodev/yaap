"""Types and constants shared by the git modules, so none of them has to import another for them."""

import re
from dataclasses import dataclass

# A remote YAAP accepts: plain https with a host and a path, and no user name or token, because git would store
# them in .git/config.
REMOTE_URL = re.compile(r"^https://(?P<host>[A-Za-z0-9.-]+)(?::\d+)?/[^\s@]+$")


def is_plain_https_remote(url: str) -> bool:
    return REMOTE_URL.match(url.strip()) is not None


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
    exit_code: int = 0
