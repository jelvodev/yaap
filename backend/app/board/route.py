"""The card's route through its stages: its plan, its build and the review of the build, end to end.

The card's side of plans and builds. `plans/` knows nothing of cards: it tells its listeners about a plan's events and
`on_plan_event` reacts, writing only what the stage owns in the card file (its plan link and `## Plan`, its branch, its
cost, its review link) and moving it through the pure `flow.step`. Each write re-reads the card at the board's head;
a card file that is broken is skipped with a warning and never rewritten. The actions (*Plan*, *Approve and build*,
*Continue*, *Stop*, *Review*, *Mark done*) are the user's: the chat's card part and the board call them.
"""

import asyncio
import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

import anyio
from sqlalchemy import select

from app.board.card import Card, revise
from app.board.costs import card_cost
from app.board.errors import (
    ActionRunning,
    BoardError,
    BoardNotStarted,
    BoardOff,
    CardBroken,
    InvalidCard,
    MoveRefused,
    NoPlan,
    NothingToReviewOnCard,
)
from app.board.flow import CardEvent, CardFacts, Refusal, step
from app.board.links import conversations_of, with_branch, with_plan, with_review
from app.board.models import BoardCardConversation
from app.board.service import BoardService
from app.board.stage_text import built_plan_markdown, card_as_data, escape_headings
from app.board.views import CardDetail
from app.core.background import BackgroundWork
from app.core.reply_services import ReplyServices
from app.plans.command import MAX_REQUEST as MAX_PLAN_REQUEST
from app.plans.markdown import plan_markdown
from app.plans.model import PlanEvent, PlanStatus
from app.plans.models import PlanRecord
from app.plans.runner import BuildRunner
from app.plans.service import PlanNotFound, PlanService, PlanView
from app.reviews.criteria_checks import card_criteria, check_on_copy, criteria_commands
from app.reviews.evidence import NO_PREVIEW, Evidence, TraceRef, assemble, evidence_from_json, evidence_markdown
from app.reviews.evidence import plan_checks as plan_check_runs
from app.reviews.scope import BuildTarget, NothingToReview
from app.reviews.service import ReviewService
from app.storage.models import Review

logger = logging.getLogger(__name__)

# The plan events the card follows.
FOLLOWED: frozenset[PlanEvent] = frozenset({"submit", "approve", "all_steps_done", "step_failed", "stop"})
# Plans that still hold the card in Plan: a new plan is not made beside them.
PLAN_IN_HAND: frozenset[PlanStatus] = frozenset({"draft", "approved", "queued", "building"})
# The /plan message holds the card; it must stay within what /plan accepts.
PLAN_TEXT_LIMIT = MAX_PLAN_REQUEST - 100


class ConversationStarter(Protocol):
    """Starts the conversation of a stage: made in the project, then its first message sent as the user's."""

    def create(self, project_id: int, title: str) -> int: ...

    def send(self, conversation_id: int, text: str) -> None: ...

    def busy(self, conversation_id: int) -> bool: ...


def can_plan(facts: CardFacts) -> bool:
    """Plan runs on a card in Design, or in Plan when its plan is gone, cancelled or ended."""
    if facts.broken:
        return False
    return facts.column == "design" or (facts.column == "plan" and facts.plan_status not in PLAN_IN_HAND)


def with_evidence(card: Card, evidence: Evidence | None, now: datetime) -> Card:
    """The card with `## Evidence` as the summary of its review's evidence, when there is any."""
    changes: dict[str, object] = {"updated": now}
    if evidence is not None:
        changes["evidence"] = evidence_markdown(evidence)
    return revise(card, changes)


def send_back_reason(
    findings: list[dict[str, object]], evidence: Evidence | None, finding: int | None, criterion: int | None
) -> str | None:
    """The reason a card is sent back with: one finding of its review, or one criterion that failed or was judged
    not met; None when the choice names neither."""
    if finding is not None and 0 <= finding < len(findings):
        item = findings[finding]
        line = item.get("line")
        where = f"{item.get('path')}:{line}" if line else str(item.get("path"))
        return f"Review finding ({item.get('severity')}) in {where}: {item.get('explanation')}"
    if criterion is not None and evidence is not None:
        for item in evidence.criteria:
            if item.number == criterion and item.status in ("failed", "judged_not_met"):
                note = f" {item.note}" if item.note else ""
                return f"Acceptance criterion {item.number} is not met: {item.text}.{note}"
    return None


def refused(outcome: Refusal) -> MoveRefused:
    return MoveRefused(outcome.code, outcome.reason)


@dataclass
class CardRoute:
    board: BoardService
    services: Callable[[], ReplyServices]
    builds: BuildRunner
    starter: ConversationStarter
    # Where plan events are handed over to: submit and approve are told from a worker thread.
    loop: asyncio.AbstractEventLoop | None = None
    work: BackgroundWork[None] = field(default_factory=BackgroundWork)
    # Cards whose review is starting: its criteria checks run before the review's row says it is running.
    starting_reviews: set[tuple[int, int]] = field(default_factory=set)
    review_lock: threading.Lock = field(default_factory=threading.Lock)

    def plans(self) -> PlanService:
        return PlanService(self.services().session_factory)

    # --- Listening to plans --------------------------------------------------------------------------------------

    def on_plan_event(self, plan_id: int, event: PlanEvent) -> None:
        """A plan listener: the card's reaction runs in the background, on the event loop."""
        if event in FOLLOWED and self.loop is not None:
            # A coroutine, not a plain callback: the background work needs a running task to open its cancel scope.
            asyncio.run_coroutine_threadsafe(self.start_reaction(plan_id, event), self.loop)

    async def start_reaction(self, plan_id: int, event: PlanEvent) -> None:
        self.work.start(self.react_later(plan_id, event), name=f"card-route-{plan_id}-{event}")

    async def react_later(self, plan_id: int, event: PlanEvent) -> None:
        try:
            await anyio.to_thread.run_sync(self.react, plan_id, event)
        except Exception:  # noqa: BLE001 - a background reaction: the card stays as it was and the cause is logged
            logger.exception("The card of plan %s could not follow its %s.", plan_id, event)

    def react(self, plan_id: int, event: PlanEvent) -> None:
        """Write the end of a stage into the plan's card. Blocking."""
        try:
            view = self.plans().get(plan_id)
        except PlanNotFound:
            return
        card_id = self.plan_card(view) if event == "submit" else view.card_id
        if card_id is None:
            return
        try:
            if event == "submit":
                self.plan_submitted(view, card_id)
            elif event == "approve":
                self.plan_approved(view, card_id)
            elif event == "all_steps_done":
                self.build_done(view, card_id)
            else:
                self.build_ended(view, card_id)
        except (BoardOff, BoardNotStarted):
            return  # The board is off: reconcile_plans links the plan when it is switched on again.
        except CardBroken as error:
            logger.warning("Card #%s was not updated after plan %s's %s: %s", card_id, plan_id, event, error)
        except BoardError as error:
            logger.warning("Card #%s did not follow plan %s's %s: %s", card_id, plan_id, event, error)

    def plan_card(self, view: PlanView) -> int | None:
        """The card whose plan conversation the plan was submitted in."""
        with self.services().session_factory() as session:
            return session.scalar(
                select(BoardCardConversation.card_id).where(
                    BoardCardConversation.conversation_id == view.conversation_id,
                    BoardCardConversation.project_id == view.project_id,
                    BoardCardConversation.role == "plan",
                )
            )

    def write_stage(self, project_id: int, card_id: int, change: Callable[[Card], Card | None]) -> None:
        opened = self.board.opened(project_id)
        self.board.write_card(opened, card_id, change, {"changed": 1})

    def moved_by(self, project_id: int, card: Card, event: CardEvent) -> Card:
        """The card in the column the flow gives for a stage's end, or MoveRefused."""
        outcome = step(self.board.facts(project_id, card), event, "yaap")
        if isinstance(outcome, Refusal):
            raise refused(outcome)
        return card if outcome.column == card.column else revise(card, {"column": outcome.column})

    def plan_submitted(self, view: PlanView, card_id: int) -> None:
        """The plan is the card's: linked both ways, `## Plan` written, the card in Plan."""

        def change(card: Card) -> Card:
            moved = self.moved_by(view.project_id, card, "plan_submitted")
            plan = escape_headings(plan_markdown(view.plan))
            return revise(with_plan(moved, view.id), {"plan": plan, "updated": self.board.clock()})

        self.write_stage(view.project_id, card_id, change)
        with self.services().session_factory() as session:
            record = session.get(PlanRecord, view.id)
            if record is not None:
                record.card_id = card_id
                session.commit()

    def plan_approved(self, view: PlanView, card_id: int) -> None:
        """The card goes into Build and names the build's branch. Once only: a card in Build is left as it is."""

        def change(card: Card) -> Card | None:
            if card.column != "plan" or card.links.plan_id != view.id:
                return None
            moved = self.moved_by(view.project_id, card, "plan_approved")
            return revise(with_branch(moved, view.build_branch), {"updated": self.board.clock()})

        self.write_stage(view.project_id, card_id, change)

    def build_done(self, view: PlanView, card_id: int) -> None:
        """The build is done: cost and `## Plan` with ticks and commits, the card into Review, then its review."""
        self.write_stage(view.project_id, card_id, self.build_written(view, card_id, move=True))
        try:
            self.start_review(view, card_id)
        except (NothingToReviewOnCard, ActionRunning) as error:
            logger.info("Card #%s was not reviewed after its build: %s", card_id, error)

    def build_ended(self, view: PlanView, card_id: int) -> None:
        """A failed or stopped build: the card stays in Build with what was done so far."""
        self.write_stage(view.project_id, card_id, self.build_written(view, card_id, move=False))

    def build_written(self, view: PlanView, card_id: int, move: bool) -> Callable[[Card], Card | None]:
        cost = self.linked_cost(view.project_id, card_id)

        def change(card: Card) -> Card | None:
            if card.links.plan_id != view.id:
                return None  # The card has another plan by now; this build's end is not its stage any more.
            moved = self.moved_by(view.project_id, card, "build_done") if move else card
            plan = built_plan_markdown(view.plan, view.build_branch)
            return revise(moved, {"plan": plan, "cost_usd": max(card.cost_usd, cost), "updated": self.board.clock()})

        return change

    def linked_cost(self, project_id: int, card_id: int) -> float:
        with self.services().session_factory() as session:
            ids = [conversation_id for conversation_id, _title, _role in conversations_of(session, project_id, card_id)]
            return card_cost(session, ids)

    # --- The user's actions --------------------------------------------------------------------------------------

    def card_and_facts(self, project_id: int, card_id: int) -> tuple[Card, CardFacts]:
        opened = self.board.opened(project_id)
        card = self.board.read_card(opened.repository, card_id, opened.repository.head())
        return card, self.board.facts(project_id, card)

    def plan(self, project_id: int, card_id: int) -> int:
        """*Plan*: a new conversation linked with role `plan`, whose first message is /plan with the card as data.
        Returns its id. Blocking."""
        card, facts = self.card_and_facts(project_id, card_id)
        if not can_plan(facts):
            raise MoveRefused("wrong_column", "Plan works on a card in Design, or in Plan when it has no plan.")
        with self.services().session_factory() as session:
            linked = conversations_of(session, project_id, card_id)
        if any(role == "plan" and self.starter.busy(conversation_id) for conversation_id, _title, role in linked):
            raise ActionRunning()
        conversation_id = self.starter.create(project_id, f"#{card.id} · Plan · {card.title}")
        self.board.link_conversation(project_id, card_id, conversation_id, "plan")
        self.starter.send(conversation_id, f"/plan {card_as_data(card, PLAN_TEXT_LIMIT)}")
        return conversation_id

    def approve(self, project_id: int, card_id: int) -> int:
        """*Approve and build*: the card's draft plan is approved and its copy made. Returns the plan's id; the caller
        launches the build on the event loop. Blocking."""
        card, facts = self.card_and_facts(project_id, card_id)
        plan_id = card.links.plan_id
        if card.column != "plan" or plan_id is None or facts.plan_status != "draft":
            raise NoPlan("The card has no draft plan to approve.")
        outcome = step(facts, "plan_approved", "user")
        if isinstance(outcome, Refusal):
            raise refused(outcome)
        view = self.builds.approve(plan_id)
        self.plan_approved(view, card_id)
        return plan_id

    def resumable(self, project_id: int, card_id: int) -> int:
        """The plan of a card whose build failed or was interrupted, made building again. Blocking."""
        card, facts = self.card_and_facts(project_id, card_id)
        if card.column != "build" or card.links.plan_id is None or facts.plan_status not in ("interrupted", "failed"):
            raise NoPlan("The card has no interrupted or failed build to continue.")
        self.builds.resume(card.links.plan_id)
        return card.links.plan_id

    def building(self, project_id: int, card_id: int) -> int:
        card, facts = self.card_and_facts(project_id, card_id)
        if card.links.plan_id is None or facts.plan_status != "building":
            raise NoPlan("The card's plan is not being built.")
        return card.links.plan_id

    async def build(self, project_id: int, card_id: int) -> CardDetail:
        plan_id = await anyio.to_thread.run_sync(self.approve, project_id, card_id)
        self.builds.launch(plan_id)
        return await anyio.to_thread.run_sync(self.board.get_card, project_id, card_id)

    async def continue_build(self, project_id: int, card_id: int) -> CardDetail:
        plan_id = await anyio.to_thread.run_sync(self.resumable, project_id, card_id)
        self.builds.launch(plan_id)
        return await anyio.to_thread.run_sync(self.board.get_card, project_id, card_id)

    async def stop(self, project_id: int, card_id: int) -> CardDetail:
        plan_id = await anyio.to_thread.run_sync(self.building, project_id, card_id)
        await self.builds.stop(plan_id)
        return await anyio.to_thread.run_sync(self.board.get_card, project_id, card_id)

    def review(self, project_id: int, card_id: int) -> int:
        """*Review* (and *Review again*): the build branch since its base commit, by another vendor, else by the same
        vendor's strongest other model (choose_reviewer()). Returns the review's id. Call it from a worker thread."""
        card, _facts = self.card_and_facts(project_id, card_id)
        if card.column != "review":
            raise MoveRefused("wrong_column", "Review works on a card in Review.")
        if card.links.plan_id is None:
            raise NoPlan()
        try:
            view = self.plans().get(card.links.plan_id)
        except PlanNotFound as error:
            raise NoPlan("The card's plan is gone, so its build cannot be found.") from error
        return self.start_review(view, card_id)

    def start_review(self, view: PlanView, card_id: int) -> int:
        """Start the review of the plan's build and link it on the card. In a worker thread."""
        if view.status != "done" or not view.base_commit or not view.build_branch:
            raise NoPlan("The card's build has not finished.")
        with self.review_starting(view.project_id, card_id):
            return self.begin_review(view, card_id)

    @contextmanager
    def review_starting(self, project_id: int, card_id: int) -> Iterator[None]:
        """One review start per card at a time, the automatic one after a build and a click on *Review* alike, also
        while the criteria checks run before the review's row exists."""
        key = (project_id, card_id)
        with self.review_lock:
            if key in self.starting_reviews:
                raise ActionRunning("The card's review is starting.")
            self.starting_reviews.add(key)
        try:
            yield
        finally:
            with self.review_lock:
                self.starting_reviews.discard(key)

    def begin_review(self, view: PlanView, card_id: int) -> int:
        with self.services().session_factory() as session:
            running = session.scalar(
                select(Review.id).where(
                    Review.project_id == view.project_id, Review.card_id == card_id, Review.status == "running"
                )
            )
        if running is not None:
            raise ActionRunning("The card's review is running.")
        objective = f"#{card_id}: {view.plan.title}\n\n{plan_markdown(view.plan)}"
        target = BuildTarget(view.base_commit, view.build_branch, objective, view.conversation_id, card_id)
        evidence = self.gathered_evidence(view, card_id)
        try:
            review = ReviewService(self.services(), self.review_ended)
            review_id = review.start_for_branch(view.project_id, target, evidence)
        except NothingToReview as error:
            raise NothingToReviewOnCard(str(error)) from error
        evidence_now = self.stored_evidence(review_id)
        self.write_stage(
            view.project_id,
            card_id,
            lambda card: with_evidence(with_review(card, review_id), evidence_now, self.board.clock()),
        )
        return review_id

    # --- Evidence ----------------------------------------------------------------------------------------------------

    def criteria_commands(self, view: PlanView) -> tuple[str, ...]:
        """The card's criteria commands that approving its plan approves too (BuildRunner.approved_extras); none for
        a plan without a card or when the card cannot be read. Blocking."""
        card_id = view.card_id if view.card_id is not None else self.plan_card(view)
        if card_id is None:
            return ()
        try:
            card, _facts = self.card_and_facts(view.project_id, card_id)
        except BoardError:
            return ()
        return criteria_commands(card_criteria(card.specification))

    def gathered_evidence(self, view: PlanView, card_id: int) -> Evidence:
        """YAAP's own evidence for the review: the plan's checks as the build ran them and the card's command and file
        criteria checked on the build's copy. In a worker thread."""
        try:
            card, _facts = self.card_and_facts(view.project_id, card_id)
            criteria = card_criteria(card.specification)
        except BoardError:
            criteria = []
        checks = anyio.from_thread.run(check_on_copy, self.services(), view, criteria) if criteria else []
        trace = TraceRef(view.conversation_id, view.build_run_id)
        return assemble(criteria, checks, plan_check_runs(view.plan, view.build.final_checks), trace, (NO_PREVIEW,))

    def stored_evidence(self, review_id: int) -> Evidence | None:
        with self.services().session_factory() as session:
            review = session.get(Review, review_id)
            return evidence_from_json(review.evidence) if review is not None else None

    def review_ended(self, review_id: int) -> None:
        """A card's review ended: its evidence with the reviewer's verdicts goes into the card as `## Evidence`.
        Blocking."""
        with self.services().session_factory() as session:
            review = session.get(Review, review_id)
            if review is None or review.card_id is None:
                return
            project_id, card_id, evidence = review.project_id, review.card_id, evidence_from_json(review.evidence)
        if evidence is None:
            return

        def change(card: Card) -> Card | None:
            if card.links.review_id not in (None, review_id):
                return None  # A newer review is the card's by now.
            return with_evidence(card, evidence, self.board.clock())

        try:
            self.write_stage(project_id, card_id, change)
        except (BoardOff, BoardNotStarted):
            return
        except BoardError as error:
            logger.warning("Card #%s did not get the evidence of review %s: %s", card_id, review_id, error)

    def send_back(self, project_id: int, card_id: int, finding: int | None, criterion: int | None) -> CardDetail:
        """*Send back with the finding*: the card goes back to Design with one finding or unmet criterion of its
        review in its description, so *Plan* plans the fix. The text is the stored review's, never the caller's.
        Blocking."""
        card, _facts = self.card_and_facts(project_id, card_id)
        with self.services().session_factory() as session:
            review = session.get(Review, card.links.review_id) if card.links.review_id is not None else None
            if review is None or review.project_id != project_id or review.card_id != card_id:
                raise NoPlan("The card has no review to send back from.")
            reason = send_back_reason(review.findings or [], evidence_from_json(review.evidence), finding, criterion)
        if reason is None:
            raise InvalidCard("Choose a finding or an unmet criterion of the card's review.")
        detail = self.board.apply_event(project_id, card_id, "send_back", "user", reason=reason)
        return detail if detail is not None else self.board.get_card(project_id, card_id)

    def mark_done(self, project_id: int, card_id: int) -> CardDetail | None:
        return self.board.apply_event(project_id, card_id, "mark_done", "user")
