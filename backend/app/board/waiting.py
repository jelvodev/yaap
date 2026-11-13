"""What waits for the user across projects: plans to approve, reviews to judge, questions to answer, tool approvals,
builds to continue and cards new in Inbox. Pure: the facts are read elsewhere (`app.board.waiting_facts`); this only
decides which of them wait for the user, as which kind of row, with which one action.

A card waits when its next step (`app.board.flow.next_step`) is the user's and one of the kinds below; other steps
of the user (specify, plan, review, a card waiting for another) are work the user starts when they choose, not
something that waits. A next step this module does not know (a later column action) is left out, never an error.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.board.flow import NextStep

WaitingKind = Literal[
    "approve_plan", "judge_review", "answer_question", "approve_action", "continue_build", "new_inbox"
]
# The one action of a row, as its button says it; every action opens the place where it is done.
WaitingAction = Literal["approve", "open", "answer", "continue", "triage"]

# The order of the sections, and of the rows in the list. A tool approval comes first: its run is denied after half an
# hour without a decision.
KIND_ORDER: tuple[WaitingKind, ...] = (
    "approve_action",
    "approve_plan",
    "judge_review",
    "answer_question",
    "new_inbox",
    "continue_build",
)
# The card's next step (when it is the user's) that makes it wait, by its action.
KIND_BY_NEXT: dict[str, WaitingKind] = {
    "approve": "approve_plan",
    "judge": "judge_review",
    "answer": "answer_question",
    "continue": "continue_build",
    "accept": "new_inbox",
}
ACTION_BY_KIND: dict[WaitingKind, WaitingAction] = {
    "approve_plan": "approve",
    "judge_review": "open",
    "answer_question": "answer",
    "approve_action": "open",
    "continue_build": "continue",
    "new_inbox": "triage",
}


@dataclass(frozen=True)
class CardWaitFacts:
    """A card of a project whose board is on, with what its row would show."""

    project_id: int
    card_id: int
    title: str
    next: NextStep
    origin_type: str | None = None
    # The card's own `updated` time.
    updated: datetime | None = None
    # The plan's conversation and model, and when the plan last changed state.
    plan_conversation_id: int | None = None
    plan_model: str | None = None
    plan_changed: datetime | None = None
    # The review's model and when it finished.
    review_model: str | None = None
    review_finished: datetime | None = None
    # The card's most recent conversation and its model.
    latest_conversation_id: int | None = None
    latest_model: str | None = None


@dataclass(frozen=True)
class ApprovalFacts:
    """A tool call a run waits on the user's decision for."""

    approval_id: str
    conversation_id: int
    tool: str
    summary: str
    project_id: int | None = None
    # The card the run's conversation belongs to, when its project has the board on.
    card_id: int | None = None
    model: str | None = None


@dataclass(frozen=True)
class BuildFacts:
    """An interrupted build of a plan that belongs to no card (a card's build waits through its next step)."""

    plan_id: int
    project_id: int
    conversation_id: int
    title: str
    model: str | None = None
    since: datetime | None = None


@dataclass(frozen=True)
class WaitingFacts:
    cards: tuple[CardWaitFacts, ...] = ()
    approvals: tuple[ApprovalFacts, ...] = ()
    builds: tuple[BuildFacts, ...] = ()
    # When the user last looked at the overview; None when never (every Inbox card is new).
    seen: datetime | None = None


@dataclass(frozen=True)
class WaitingItem:
    kind: WaitingKind
    project_id: int | None
    card_id: int | None
    conversation_id: int | None
    model: str | None
    since: datetime | None
    action: WaitingAction
    # The card's title, the approval's summary or the plan's title: shown as plain text.
    title: str
    # What the row says below the title: the next step, the tool or the card's origin.
    detail: str = ""
    approval_id: str | None = None
    plan_id: int | None = None


def is_new(updated: datetime | None, seen: datetime | None) -> bool:
    """Whether an Inbox card came or changed after the user last looked."""
    if seen is None:
        return True
    return updated is not None and updated > seen


def card_item(card: CardWaitFacts, seen: datetime | None) -> WaitingItem | None:
    """The card's row, or None when it does not wait for the user."""
    if card.next.who != "you":
        return None
    kind = KIND_BY_NEXT.get(card.next.action)
    if kind is None or (kind == "new_inbox" and not is_new(card.updated, seen)):
        return None
    conversation_id, model, since = card.latest_conversation_id, card.latest_model, card.updated
    if kind in ("approve_plan", "continue_build"):
        conversation_id = card.plan_conversation_id or conversation_id
        model = card.plan_model or model
        since = card.plan_changed or since
    elif kind == "judge_review":
        model = card.review_model or model
        since = card.review_finished or since
    detail = origin_detail(card.origin_type) if kind == "new_inbox" else card.next.label
    return WaitingItem(
        kind, card.project_id, card.card_id, conversation_id, model, since, ACTION_BY_KIND[kind], card.title, detail
    )


ORIGIN_DETAILS: dict[str, str] = {
    "chat": "from a conversation",
    "agent": "by the agent",
    "trigger": "by a trigger",
    "issue": "from an issue",
    "review": "from a review",
    "learning": "from learning",
}


def origin_detail(origin_type: str | None) -> str:
    """Where an Inbox card came from; a card without a known origin was added by the user."""
    return ORIGIN_DETAILS.get(origin_type or "", "added by you")


def approval_item(approval: ApprovalFacts) -> WaitingItem:
    return WaitingItem(
        "approve_action",
        approval.project_id,
        approval.card_id,
        approval.conversation_id,
        approval.model,
        None,
        ACTION_BY_KIND["approve_action"],
        approval.summary,
        approval.tool,
        approval_id=approval.approval_id,
    )


def build_item(build: BuildFacts) -> WaitingItem:
    return WaitingItem(
        "continue_build",
        build.project_id,
        None,
        build.conversation_id,
        build.model,
        build.since,
        ACTION_BY_KIND["continue_build"],
        build.title,
        "The build was interrupted",
        plan_id=build.plan_id,
    )


def sort_key(item: WaitingItem) -> tuple[int, float, int, int]:
    """By section, then oldest first (rows without a moment last), then project and card."""
    moment = item.since.timestamp() if item.since is not None else float("inf")
    return (KIND_ORDER.index(item.kind), moment, item.project_id or 0, item.card_id or 0)


def waiting_items(facts: WaitingFacts) -> list[WaitingItem]:
    """Every row of the overview, in its order."""
    found: Iterable[WaitingItem | None] = (
        *(card_item(card, facts.seen) for card in facts.cards),
        *(approval_item(approval) for approval in facts.approvals),
        *(build_item(build) for build in facts.builds),
    )
    return sorted((item for item in found if item is not None), key=sort_key)
