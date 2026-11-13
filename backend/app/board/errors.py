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
    "project_not_found",
    "board_off",
    "board_not_started",
    "board_exists",
    "no_repository",
    "board_conflict",
    "board_full",
    "card_changed",
    "move_refused",
    "already_answered",
    "card_building",
    "invalid_version",
    "push_not_allowed",
    "public_remote",
    "no_remote",
    "push_failed",
    "action_running",
    "no_plan",
    "nothing_to_review",
    "board_diverged",
    "pull_failed",
    "invalid_repository_url",
    "intake_limit",
    "already_on_board",
    "finding_not_found",
    "no_github_repository",
    "github_failed",
    "merge_refused",
]
# Why the flow (app.board.flow) refuses a move or event.
RefusalCode = Literal[
    "card_broken",
    "build_running",
    "use_send_back",
    "needs_specification",
    "use_approve",
    "waits_for",
    "build_not_finished",
    "no_review",
    "one_column",
    "not_allowed",
    "wrong_column",
    "no_target",
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


class ProjectNotFound(BoardError):
    code: ClassVar[BoardErrorCode] = "project_not_found"
    default_sentence: ClassVar[str] = "The project does not exist."


class BoardOff(BoardError):
    """The project_board setting is off for the project; the board is kept but not used."""

    code: ClassVar[BoardErrorCode] = "board_off"
    default_sentence: ClassVar[str] = "The board is switched off for this project."


class BoardNotStarted(BoardError):
    code: ClassVar[BoardErrorCode] = "board_not_started"
    default_sentence: ClassVar[str] = "This project has no board yet. Start it first."


class BoardExists(BoardError):
    code: ClassVar[BoardErrorCode] = "board_exists"
    default_sentence: ClassVar[str] = "This project already has a board."


class NoRepository(BoardError):
    code: ClassVar[BoardErrorCode] = "no_repository"
    default_sentence: ClassVar[str] = "This project has no git repository. Create one to keep the board in it."


class BoardConflict(BoardError):
    """The board's branch moved again while a change was applied a second time."""

    code: ClassVar[BoardErrorCode] = "board_conflict"
    default_sentence: ClassVar[str] = "The board kept changing while this was saved. Try again."


class BoardFull(BoardError):
    code: ClassVar[BoardErrorCode] = "board_full"
    default_sentence: ClassVar[str] = "The board has as many cards or versions as it can hold."


class CardChanged(BoardError):
    """The card changed since the caller read it (`expected_updated` differs). `current` is the card as it is now,
    set by the service so the answer can carry it."""

    code: ClassVar[BoardErrorCode] = "card_changed"
    default_sentence: ClassVar[str] = "The card changed in the meantime. Look at it again before you save."

    def __init__(self, card_id: int, current: object = None) -> None:
        self.card_id = card_id
        self.current = current
        super().__init__()


class MoveRefused(BoardError):
    """The flow refused the move or event; `refusal_code` says which rule, the sentence why."""

    code: ClassVar[BoardErrorCode] = "move_refused"

    def __init__(self, refusal_code: RefusalCode, reason: str) -> None:
        self.refusal_code = refusal_code
        super().__init__(reason)


class AlreadyAnswered(BoardError):
    code: ClassVar[BoardErrorCode] = "already_answered"
    default_sentence: ClassVar[str] = "This question has an answer already."


class CardBuilding(BoardError):
    code: ClassVar[BoardErrorCode] = "card_building"
    default_sentence: ClassVar[str] = "The card is being built. Stop the build first."


class InvalidVersion(BoardError):
    code: ClassVar[BoardErrorCode] = "invalid_version"
    default_sentence: ClassVar[str] = "The version is not valid."


class PushNotAllowed(BoardError):
    code: ClassVar[BoardErrorCode] = "push_not_allowed"
    default_sentence: ClassVar[str] = "Pushing the board is switched off for this project (the board_push setting)."


class PublicRemote(BoardError):
    """The remote is public, or YAAP cannot tell: pushing would publish every card, so the user must confirm."""

    code: ClassVar[BoardErrorCode] = "public_remote"
    default_sentence: ClassVar[str] = (
        "The remote may be public: pushing the board publishes every card, specification and plan. Confirm to push."
    )


class NoRemote(BoardError):
    code: ClassVar[BoardErrorCode] = "no_remote"
    default_sentence: ClassVar[str] = "This repository has no remote yet. Set a GitHub URL first."


class PushFailed(BoardError):
    code: ClassVar[BoardErrorCode] = "push_failed"
    default_sentence: ClassVar[str] = "The board could not be pushed."


class ActionRunning(BoardError):
    """A stage of the card is already running (its plan conversation is replying, or its review runs)."""

    code: ClassVar[BoardErrorCode] = "action_running"
    default_sentence: ClassVar[str] = "This stage of the card is already running. Wait for it to finish."


class MergeRefused(BoardError):
    code: ClassVar[BoardErrorCode] = "merge_refused"
    default_sentence: ClassVar[str] = "This card cannot be merged into its version now."


class NoPlan(BoardError):
    code: ClassVar[BoardErrorCode] = "no_plan"
    default_sentence: ClassVar[str] = "The card has no plan for this. Make its plan first."


class NothingToReviewOnCard(BoardError):
    code: ClassVar[BoardErrorCode] = "nothing_to_review"
    default_sentence: ClassVar[str] = "The build branch has no changes to review."


class IntakeLimit(BoardError):
    """A limit on cards that come from outside the user's hand (the agent, issues, findings) is reached."""

    code: ClassVar[BoardErrorCode] = "intake_limit"
    default_sentence: ClassVar[str] = "No more cards can be added to Inbox now."


class AlreadyOnBoard(BoardError):
    """The thing a card is asked for (a review's finding) is a card already."""

    code: ClassVar[BoardErrorCode] = "already_on_board"

    def __init__(self, card_id: int) -> None:
        self.card_id = card_id
        super().__init__(f"This is card #{card_id} already.")


class FindingNotFound(BoardError):
    code: ClassVar[BoardErrorCode] = "finding_not_found"
    default_sentence: ClassVar[str] = "The review or its finding does not exist in this project."


class NoGitHubRepository(BoardError):
    code: ClassVar[BoardErrorCode] = "no_github_repository"
    default_sentence: ClassVar[str] = "This project's repository is not on GitHub, so it has no issues to import."


class GitHubFailed(BoardError):
    """GitHub could not be asked or refused; the sentence is GitHub's own message, which holds no secret."""

    code: ClassVar[BoardErrorCode] = "github_failed"
    default_sentence: ClassVar[str] = "GitHub could not be asked for the issues."


class BoardDiverged(BoardError):
    """The board here and its separate repository both have commits the other lacks; a pull only fast-forwards."""

    code: ClassVar[BoardErrorCode] = "board_diverged"
    default_sentence: ClassVar[str] = (
        "The board here and its repository have both changed since they were last in step, so it cannot be pulled "
        "without merging. Nothing was changed."
    )


class PullFailed(BoardError):
    """The separate repository could not be cloned or fetched (network, access, a refused URL)."""

    code: ClassVar[BoardErrorCode] = "pull_failed"
    default_sentence: ClassVar[str] = "The board's repository could not be reached."


class InvalidRepositoryUrl(BoardError):
    code: ClassVar[BoardErrorCode] = "invalid_repository_url"
    default_sentence: ClassVar[str] = (
        "Use an https URL without a user name or token in it, for example https://github.com/owner/board.git."
    )
