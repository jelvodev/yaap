"""The board's errors. Each has a stable `code` the API answers with and a sentence that is safe to show: it never
holds file content, a secret or a path outside the board."""

from typing import ClassVar, Literal

BoardErrorCode = Literal[
    "head_moved",
    "card_broken",
    "card_not_found",
    "invalid_card",
    "board_unavailable",
    "path_refused",
    "board_checked_out",
]


class BoardError(Exception):
    """Base of the board's errors; `str(error)` is the safe sentence."""

    code: ClassVar[BoardErrorCode] = "board_unavailable"
    default_sentence: ClassVar[str] = "The board could not be used."

    def __init__(self, sentence: str | None = None) -> None:
        self.sentence = sentence or self.default_sentence
        super().__init__(self.sentence)


class HeadMoved(BoardError):
    """The board's branch is no longer at the commit a change was based on (compare-and-swap failed)."""

    code: ClassVar[BoardErrorCode] = "head_moved"
    default_sentence: ClassVar[str] = "The board changed in the meantime."

    def __init__(self, current: str | None) -> None:
        self.current = current
        super().__init__()


class CardBroken(BoardError):
    """The card's file cannot be read as a card; YAAP shows it with its error and never rewrites it."""

    code: ClassVar[BoardErrorCode] = "card_broken"

    def __init__(self, card_id: int, reason: str) -> None:
        self.card_id = card_id
        self.reason = reason
        super().__init__(f"Card #{card_id} cannot be read: {reason}")


class CardNotFound(BoardError):
    code: ClassVar[BoardErrorCode] = "card_not_found"

    def __init__(self, card_id: int) -> None:
        self.card_id = card_id
        super().__init__(f"Card #{card_id} does not exist.")


class InvalidCard(BoardError):
    """A card or version that is about to be written does not meet the file format's rules."""

    code: ClassVar[BoardErrorCode] = "invalid_card"
    default_sentence: ClassVar[str] = "The card is not valid."


class BoardUnavailable(BoardError):
    """Git could not read or write the board. `detail` (git's own message) is for the log, not for the answer."""

    code: ClassVar[BoardErrorCode] = "board_unavailable"
    default_sentence: ClassVar[str] = "The board's branch could not be read or written."

    def __init__(self, sentence: str | None = None, detail: str = "") -> None:
        self.detail = detail
        super().__init__(sentence)


class PathRefused(BoardError):
    """A path, ref or commit id outside the board's allow list."""

    code: ClassVar[BoardErrorCode] = "path_refused"
    default_sentence: ClassVar[str] = "That path is not a board file."


class BoardCheckedOut(BoardError):
    """The board's branch is checked out in a work tree, so writing it would change that work tree's files."""

    code: ClassVar[BoardErrorCode] = "board_checked_out"
    default_sentence: ClassVar[str] = "The board branch is checked out; switch away from it."
