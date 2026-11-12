"""The card's way through the columns: one pure state machine for every move, by hand or at the end of a stage.

`step` decides whether an event moves a card and where to; `next_step` says what is needed next; `card_state` gives
the tile's state. Who causes an event is an argument (`actor`), so a wider autonomy later only adds actors. Nothing
here reads git, the database or a clock."""

from dataclasses import dataclass
from typing import Literal

from app.board.card import COLUMNS, Column
from app.board.errors import RefusalCode
from app.plans.model import PlanStatus

Actor = Literal["user", "agent", "trigger", "yaap"]
CardEvent = Literal[
    "accept",
    "drop",
    "specify_done",
    "plan_submitted",
    "plan_approved",
    "build_done",
    "build_failed",
    "review_done",
    "send_back",
    "mark_done",
    "move",
]
ReviewState = Literal["none", "running", "done"]
NextAction = Literal[
    "accept", "specify", "plan", "approve", "build", "continue", "review", "judge", "answer", "merge", "none"
]
Who = Literal["you", "yaap"]
CardState = Literal["broken", "working", "failed", "interrupted", "waiting", "idle"]

# Plan states in which a build holds the card.
RUNNING_PLANS: frozenset[PlanStatus] = frozenset({"building"})
# The note a send back writes above the reason in the card's description.
SENT_BACK_HEADING = "Sent back"


@dataclass(frozen=True)
class CardFacts:
    column: Column
    plan_status: PlanStatus | None = None
    review: ReviewState = "none"
    # Whether the review's evidence holds; None while there is none.
    evidence_ok: bool | None = None
    # Cards this one waits for that are not done yet.
    open_waits: tuple[int, ...] = ()
    open_questions: int = 0
    has_specification: bool = False
    broken: bool = False


@dataclass(frozen=True)
class Transition:
    """The card's column after the event (the same column when it stays) and what happened, for the log."""

    column: Column
    note: str = ""


@dataclass(frozen=True)
class Refusal:
    code: RefusalCode
    reason: str


@dataclass(frozen=True)
class NextStep:
    action: NextAction
    who: Who
    label: str


def review_from_status(status: str | None) -> ReviewState:
    """The flow's view of a review row's status (running, done, skipped or failed)."""
    if status == "running":
        return "running"
    return "done" if status == "done" else "none"


def position(column: Column) -> int:
    return COLUMNS.index(column)


def waits_sentence(open_waits: tuple[int, ...]) -> str:
    return "Waits for " + ", ".join(f"#{card_id}" for card_id in open_waits) + "."


def wrong_column(event: CardEvent, column: Column) -> Refusal:
    return Refusal("wrong_column", f"A card in {column.capitalize()} cannot take {event.replace('_', ' ')}.")


def step(facts: CardFacts, event: CardEvent, actor: Actor, target: Column | None = None) -> Transition | Refusal:
    """Where the event takes the card, or why it is refused. Any pair the table does not name is refused."""
    if facts.broken:
        return Refusal("card_broken", "The card's file cannot be read; fix it on the board's branch first.")
    if actor in ("agent", "trigger") and not (event == "move" and target == "inbox"):
        return Refusal("not_allowed", "The agent adds cards to Inbox; it does not move them.")
    if event == "move":
        if target is None:
            return Refusal("no_target", "Say which column the card moves to.")
        return move(facts, target, actor)
    if event == "drop":
        if facts.plan_status in RUNNING_PLANS:
            return Refusal("build_running", "Stop the build first.")
        return Transition(facts.column, "dropped")
    return stage_event(facts, event, actor, target)


def stage_event(facts: CardFacts, event: CardEvent, actor: Actor, target: Column | None) -> Transition | Refusal:
    """The events that end or start a stage: they move a card at most one column, as the table says."""
    column = facts.column
    if event == "accept":
        if column != "inbox":
            return wrong_column(event, column)
        into = target or "idea"
        if into not in ("idea", "design"):
            return Refusal("one_column", "Accept moves a card into Idea or Design.")
        return Transition(into, "accepted")
    if event == "specify_done":
        if column == "idea" and facts.has_specification:
            return Transition("design", "specified")
        if column in ("idea", "design"):
            return Transition(column, "specification saved")
        return wrong_column(event, column)
    if event == "plan_submitted":
        if column in ("design", "plan"):
            return Transition("plan", "plan submitted")
        return wrong_column(event, column)
    if event == "plan_approved":
        if column != "plan":
            return wrong_column(event, column)
        if facts.open_waits:
            return Refusal("waits_for", waits_sentence(facts.open_waits))
        return Transition("build", "plan approved")
    if event in ("build_done", "build_failed"):
        if column != "build":
            return wrong_column(event, column)
        if event == "build_failed":
            return Transition("build", "build failed")
        if facts.plan_status != "done":
            return Refusal("build_not_finished", "The build has not finished.")
        return Transition("review", "built")
    if event == "review_done":
        return Transition("review", "reviewed") if column == "review" else wrong_column(event, column)
    if event == "send_back":
        return send_back(facts)
    if event == "mark_done":
        if column != "review":
            return wrong_column(event, column)
        if actor != "user" and facts.review != "done":
            return Refusal("no_review", "No review has run. Use Mark done to skip it.")
        return Transition("done", "marked done")
    return wrong_column(event, column)


def send_back(facts: CardFacts) -> Transition | Refusal:
    """From a failed or interrupted build, or from Review, back to Design: the plan must be made again."""
    if facts.column == "review" or (facts.column == "build" and facts.plan_status in ("failed", "interrupted")):
        return Transition("design", "sent back")
    if facts.column == "build":
        return Refusal("build_running", "Only a failed or interrupted build can be sent back; stop it first.")
    return wrong_column("send_back", facts.column)


def move(facts: CardFacts, target: Column, actor: Actor) -> Transition | Refusal:
    """A move by hand (or the agent's one move, back into Inbox), checked against the table."""
    column = facts.column
    if target == column:
        return Transition(column)
    if position(target) < position(column):
        if facts.plan_status in RUNNING_PLANS:
            return Refusal("build_running", "Stop the build first.")
        if target == "plan" and facts.plan_status != "draft":
            return Refusal("use_send_back", "Use Send back: the plan must be made again.")
        return Transition(target, "moved back")
    pair = (column, target)
    if pair in (("inbox", "idea"), ("inbox", "design"), ("idea", "design")):
        return Transition(target, "moved")
    if pair == ("design", "plan"):
        if not facts.has_specification and actor != "user":
            return Refusal("needs_specification", "Write a specification first, or move it yourself.")
        return Transition(target, "moved")
    if pair == ("plan", "build"):
        return Refusal("use_approve", "Use Approve and build on the plan.")
    if pair == ("build", "review"):
        if facts.plan_status != "done":
            return Refusal("build_not_finished", "The build has not finished.")
        return Transition(target, "moved")
    if pair == ("review", "done"):
        if facts.review != "done":
            return Refusal("no_review", "No review has run. Use Mark done to skip it.")
        return Transition(target, "moved")
    return Refusal("one_column", "Cards move one column at a time.")


def next_step(facts: CardFacts) -> NextStep:
    """What is needed next for the card, and who does it."""
    if facts.broken:
        return NextStep("none", "you", "Fix the card's file on the board's branch")
    if facts.open_questions:
        return NextStep("answer", "you", "Answer the open question")
    column = facts.column
    if column == "inbox":
        return NextStep("accept", "you", "Accept or drop")
    if column == "idea":
        return NextStep("specify", "you", "Write the specification")
    if column == "design":
        return NextStep("plan", "you", "Make the plan")
    if column == "plan":
        return plan_step(facts)
    if column == "build":
        return build_step(facts)
    if column == "review":
        if facts.review == "running":
            return NextStep("none", "yaap", "Reviewing")
        if facts.review == "done":
            label = "Judge the review" if facts.evidence_ok is not False else "Judge the review: it found problems"
            return NextStep("judge", "you", label)
        return NextStep("review", "you", "Review the build")
    return NextStep("none", "you", "Done")


def plan_step(facts: CardFacts) -> NextStep:
    status = facts.plan_status
    if status == "draft":
        if facts.open_waits:
            return NextStep("none", "you", waits_sentence(facts.open_waits))
        return NextStep("approve", "you", "Approve and build")
    if status in ("approved", "queued"):
        return NextStep("build", "yaap", "Waiting to build")
    if status == "building":
        return NextStep("none", "yaap", "Building")
    return NextStep("plan", "you", "Plan again")


def build_step(facts: CardFacts) -> NextStep:
    status = facts.plan_status
    if status == "building":
        return NextStep("none", "yaap", "Building")
    if status in ("approved", "queued"):
        return NextStep("build", "yaap", "Waiting to build")
    if status == "interrupted":
        return NextStep("continue", "you", "Continue the build")
    if status == "failed":
        return NextStep("continue", "you", "Continue the build or send it back")
    if status == "done":
        return NextStep("review", "yaap", "Moving to Review")
    return NextStep("plan", "you", "Plan again")


def card_state(facts: CardFacts, streaming: bool = False, approval_pending: bool = False) -> CardState:
    """The tile's state. `waiting` (the yellow edge) means the card waits for the user."""
    if facts.broken:
        return "broken"
    if streaming or facts.plan_status in ("building", "queued"):
        return "working" if not approval_pending else "waiting"
    if facts.plan_status == "failed" and facts.column == "build":
        return "failed"
    if facts.plan_status == "interrupted" and facts.column == "build":
        return "interrupted"
    if (
        facts.open_questions
        or (facts.column == "plan" and facts.plan_status == "draft")
        or (facts.column == "review" and facts.review == "done")
    ):
        return "waiting"
    return "idle"
