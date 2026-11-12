"""Types and constants shared by the git modules, so none of them has to import another for them."""

from dataclasses import dataclass

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
