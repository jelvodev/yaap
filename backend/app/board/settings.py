"""The board's settings: their keys and validation. Kept free of imports from `app.core.settings`, which uses them,
and from `app.git.remote`, which imports the settings through the runner."""

from typing import Any, Literal
from urllib.parse import urlsplit

from app.providers.registry import ProviderRegistry

PROJECT_BOARD_KEY = "project_board"
BOARD_PUSH_KEY = "board_push"
BOARD_STORAGE_KEY = "board_storage"
BOARD_REPOSITORY_URL_KEY = "board_repository_url"
MAX_REPOSITORY_URL = 500
URL_RULE = "Use an https URL without a user name or token in it, for example https://github.com/owner/board.git."


def validate_switch(value: Any, _providers: ProviderRegistry) -> str | None:
    """A switch is exactly true or false."""
    return None if isinstance(value, bool) else "Use true or false."


def validate_repository_url(value: Any, _providers: ProviderRegistry) -> str | None:
    """Empty (no separate repository) or a plain https URL. The full check, also against the project's own remote,
    runs where the URL is used (`app.board.remote.checked_board_url`)."""
    if value == "":
        return None
    if not isinstance(value, str) or len(value) > MAX_REPOSITORY_URL:
        return URL_RULE
    return None if plain_https(value) else URL_RULE


def plain_https(url: str) -> bool:
    """An https URL with a host and a path, and no user name, password or whitespace."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return False
    return (
        parts.scheme == "https"
        and bool(parts.hostname)
        and "@" not in parts.netloc
        and parts.path not in ("", "/")
        and not any(character.isspace() for character in url.strip())
    )


def remote_identity(url: str) -> str | None:
    """A remote as `host/owner/name` in lower case, so the https, ssh and scp-like spellings of one repository
    compare equal; None when it cannot be read as a remote."""
    text = url.strip()
    if "://" not in text and ":" in text and "@" in text.split(":", 1)[0]:
        host_part, _, path = text.partition(":")
        text = f"ssh://{host_part}/{path}"
    try:
        parts = urlsplit(text)
    except ValueError:
        return None
    if not parts.hostname:
        return None
    path = parts.path.strip("/").removesuffix(".git").strip("/")
    return f"{parts.hostname}/{path}".lower() if path else None


def same_remote(url: str, project_remote: str | None) -> bool:
    """Whether a URL names the project's own repository, where the board would be as public as the project."""
    if project_remote is None:
        return False
    identity = remote_identity(url)
    return identity is not None and identity == remote_identity(project_remote)


def push_offered(storage: Literal["branch", "repository"], board_push: bool) -> bool:
    """Push is offered for a board in its own repository (choosing that repository is the choice to push there),
    and for the board's branch only when `board_push` is on, because that branch goes to the project's remote."""
    return storage == "repository" or board_push
