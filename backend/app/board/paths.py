"""The board's allow list: which paths, refs and commit ids the board reads and writes.

Only `cards/<id>.md` and `versions/<name>.md` are board files; anything else on the branch is left alone and never
written. The ref must be a branch (`refs/heads/...`) and the commit ids plain hashes, so no value from a request can
reach git as an option, a revision expression or a path outside the board."""

import re
from dataclasses import dataclass
from typing import Literal

from app.board.errors import PathRefused

DEFAULT_REF = "refs/heads/yaap-board"
# The largest board file read or written, in bytes; a larger blob is refused before it is read.
MAX_FILE_BYTES = 200_000
MAX_CARD_ID = 999_999_999
VERSION_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}")

CARD_PATH = re.compile(r"cards/([1-9][0-9]{0,8})\.md")
VERSION_PATH = re.compile(r"versions/([A-Za-z0-9][A-Za-z0-9._-]{0,39})\.md")
# A branch name in segments of safe characters; `check-ref-format` checks it again in the repository.
REF = re.compile(r"refs/heads/[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*")
MAX_REF_LENGTH = 200
COMMIT_ID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")

FileKind = Literal["card", "version"]


@dataclass(frozen=True)
class BoardFile:
    """A board file: a card with its id or a version with its name."""

    kind: FileKind
    path: str
    card_id: int | None = None
    version: str | None = None


def classify(path: str) -> BoardFile | None:
    """The board file at path, or None for any other path."""
    card = CARD_PATH.fullmatch(path)
    if card is not None:
        return BoardFile("card", path, card_id=int(card.group(1)))
    version = VERSION_PATH.fullmatch(path)
    if version is not None:
        return BoardFile("version", path, version=version.group(1))
    return None


def check_path(path: str) -> BoardFile:
    """The board file at path; raises PathRefused for any path outside the allow list."""
    found = classify(path)
    if found is None:
        raise PathRefused()
    return found


def card_path(card_id: int) -> str:
    if isinstance(card_id, bool) or not isinstance(card_id, int) or not 1 <= card_id <= MAX_CARD_ID:
        raise PathRefused("A card id is a whole number from 1.")
    return f"cards/{card_id}.md"


def is_version_name(name: str) -> bool:
    return VERSION_NAME.fullmatch(name) is not None


def version_path(name: str) -> str:
    if not is_version_name(name):
        raise PathRefused("A version name has 1 to 40 letters, digits, dots, dashes or underscores.")
    return f"versions/{name}.md"


def check_ref(ref: str) -> str:
    """The ref when it is a plain branch ref; raises PathRefused otherwise."""
    if (
        len(ref) > MAX_REF_LENGTH
        or REF.fullmatch(ref) is None
        or ".." in ref
        or any(segment.endswith((".lock", ".")) for segment in ref.split("/"))
    ):
        raise PathRefused("The board's branch must be a branch under refs/heads/.")
    return ref


def check_commit(commit: str) -> str:
    """The commit id when it is a full hash; raises PathRefused otherwise (no names, options or expressions)."""
    if COMMIT_ID.fullmatch(commit) is None:
        raise PathRefused("A commit is given by its full hash.")
    return commit
