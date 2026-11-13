"""The column actions of the board: each runs a card's stage from a click, as the card's next step says.

*Specify* is this module's own: a conversation linked to the card with role `specify`, with the `specification` skill
pinned and the card as data in its first message; when one of its replies ends with the card's specification
written (by `update_card`), the card moves from Idea to Design. *Plan*, *Approve and build*, *Stop*, *Continue*,
*Review* and *Mark done* are the card's route (app/board/route.py); *Send back* is the route's for a review and this
module's for a build that failed or was interrupted.
"""

import asyncio
import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Literal

import anyio

from app.blueprint.levels import layers_for
from app.board.card import Card
from app.board.errors import ActionRunning, BoardError, BoardNotStarted, BoardOff, MoveRefused, NoPlan
from app.board.links import conversations_of
from app.board.route import CardRoute, ConversationStarter
from app.board.service import BoardService
from app.board.stage_text import specify_text
from app.board.views import CardDetail
from app.core.background import BackgroundWork
from app.core.reply_services import ReplyServices
from app.plans.service import PlanNotFound
from app.specs.templates import SKILL_NAME, template
from app.storage.models import Conversation, Environment, Project

logger = logging.getLogger(__name__)

CardAction = Literal["specify", "plan", "build", "continue", "stop", "review", "mark_done"]
# The first message of a Specify conversation holds the template and the card; the card is cut to stay within it.
SPECIFY_TEXT_LIMIT = 40_000
# How long a Specify conversation is followed for a reply that writes the specification, and how often it is looked
# at for a new reply. After a restart it is not followed any more: the card is then moved by hand.
FOLLOW_SECONDS = 24 * 60 * 60
FOLLOW_POLL_SECONDS = 2.0


class WaitsForOpen(MoveRefused):
    """The card's plan cannot be approved while cards it waits for are not done; `waits_for` names them."""

    def __init__(self, waits_for: tuple[int, ...]) -> None:
        self.waits_for = waits_for
        names = ", ".join(f"#{card_id}" for card_id in waits_for)
        super().__init__("waits_for", f"Waits for {names}. Finish those cards first.")


@dataclass(frozen=True)
class ActionResult:
    card: CardDetail
    # The conversation Specify or Plan started.
    conversation_id: int | None = None
    # The review Review started.
    review_id: int | None = None


def failed_build_reason(status: str | None, failure: str | None) -> str:
    """What a card sent back from Build says in its description: why its build stopped."""
    if status == "interrupted":
        return "The build was interrupted (YAAP restarted or the build was stopped); plan it again."
    return f"The build failed: {failure.strip()}" if failure and failure.strip() else "The build failed."


@dataclass
class ActionService:
    board: BoardService
    route: CardRoute
    starter: ConversationStarter
    services: Callable[[], ReplyServices]
    # The Specify conversations being followed until a reply writes the specification.
    work: BackgroundWork[None] = field(default_factory=BackgroundWork)
    # Cards whose review is starting: its criteria checks run before the review's row says it is running.
    starting_reviews: set[tuple[int, int]] = field(default_factory=set)
    lock: threading.Lock = field(default_factory=threading.Lock)

    async def run(self, project_id: int, card_id: int, action: CardAction) -> ActionResult:
        """Run one stage action on the card. Raises a BoardError, or BuildRefused when a build cannot start."""
        if action == "specify":
            conversation_id = await self.specify(project_id, card_id)
            return await self.result(project_id, card_id, conversation_id=conversation_id)
        if action == "plan":
            conversation_id = await anyio.to_thread.run_sync(self.route.plan, project_id, card_id)
            return await self.result(project_id, card_id, conversation_id=conversation_id)
        if action == "build":
            await anyio.to_thread.run_sync(self.check_waits, project_id, card_id)
            return ActionResult(await self.route.build(project_id, card_id))
        if action == "continue":
            return ActionResult(await self.route.continue_build(project_id, card_id))
        if action == "stop":
            return ActionResult(await self.route.stop(project_id, card_id))
        if action == "review":
            review_id = await anyio.to_thread.run_sync(self.review, project_id, card_id)
            return await self.result(project_id, card_id, review_id=review_id)
        detail = await anyio.to_thread.run_sync(self.route.mark_done, project_id, card_id)
        return ActionResult(detail) if detail is not None else await self.result(project_id, card_id)

    async def result(
        self, project_id: int, card_id: int, conversation_id: int | None = None, review_id: int | None = None
    ) -> ActionResult:
        detail = await anyio.to_thread.run_sync(self.board.get_card, project_id, card_id)
        return ActionResult(detail, conversation_id, review_id)

    # --- Specify -------------------------------------------------------------------------------------------------

    async def specify(self, project_id: int, card_id: int) -> int:
        """*Specify*: start the card's Specify conversation and follow it until a reply writes the specification."""
        conversation_id, before = await anyio.to_thread.run_sync(self.start_specify, project_id, card_id)
        self.work.start(
            self.follow_specify(project_id, card_id, conversation_id, before), name=f"specify-{project_id}-{card_id}"
        )
        return conversation_id

    def start_specify(self, project_id: int, card_id: int) -> tuple[int, str]:
        """A new conversation linked with role `specify`, the skill pinned, its first message sent. Returns its id and
        the specification as it was. Blocking."""
        card, _facts = self.route.card_and_facts(project_id, card_id)
        if card.column != "idea":
            raise MoveRefused("wrong_column", "Specify works on a card in Idea.")
        with self.services().session_factory() as session:
            linked = conversations_of(session, project_id, card_id)
        if any(role == "specify" and self.starter.busy(conversation_id) for conversation_id, _title, role in linked):
            raise ActionRunning()
        conversation_id = self.starter.create(project_id, f"#{card.id} · Specify · {card.title}")
        self.board.link_conversation(project_id, card_id, conversation_id, "specify")
        text = specify_text(card, self.pin_skill(conversation_id, card), SPECIFY_TEXT_LIMIT)
        self.starter.send(conversation_id, text)
        return conversation_id, card.specification

    def pin_skill(self, conversation_id: int, card: Card) -> str:
        """Pin the specification skill in the conversation; returns the template for the card's kind from the same
        Blueprint layers. A layer without the skill leaves it unpinned (the reply skips unknown skills)."""
        services = self.services()
        with services.session_factory() as session:
            conversation = session.get_one(Conversation, conversation_id)
            environment = session.get(Environment, conversation.environment_id)
            project = session.get(Project, conversation.project_id) if conversation.project_id is not None else None
            layers = layers_for(services.blueprint_dir, services.workspace_dir, environment, project)
            conversation.pinned_skills = [SKILL_NAME]
            session.commit()
        return template(card.kind, layers)

    async def follow_specify(self, project_id: int, card_id: int, conversation_id: int, before: str) -> None:
        """After each reply of the Specify conversation: when the specification changed, the card moves on."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + FOLLOW_SECONDS
        seen: str | None = None
        try:
            while loop.time() < deadline:
                job = self.services().replies.jobs.get(conversation_id)
                if job is None or job.id == seen or job.task is None:
                    await asyncio.sleep(FOLLOW_POLL_SECONDS)
                    continue
                seen = job.id
                await asyncio.wait({job.task})
                if await anyio.to_thread.run_sync(self.specified, project_id, card_id, before):
                    return
        except Exception:  # noqa: BLE001 - background work: the card stays in Idea, to be moved by hand; logged
            logger.exception("The Specify conversation of card #%s could not be followed.", card_id)

    def specified(self, project_id: int, card_id: int, before: str) -> bool:
        """Move the card from Idea to Design when its specification was written. True when there is nothing more to
        follow: the card moved, left Idea or cannot be read. Blocking."""
        try:
            card, _facts = self.route.card_and_facts(project_id, card_id)
            if card.column != "idea":
                return True
            if not card.specification.strip() or card.specification == before:
                return False
            self.board.apply_event(project_id, card_id, "specify_done", "yaap")
        except (BoardOff, BoardNotStarted):
            return True
        except BoardError as error:
            logger.warning("Card #%s did not move on after its specification: %s", card_id, error)
        return True

    # --- Build and review ----------------------------------------------------------------------------------------

    def check_waits(self, project_id: int, card_id: int) -> None:
        """*Approve and build* on a card that waits for cards not done yet is refused with their numbers. Blocking."""
        card, facts = self.route.card_and_facts(project_id, card_id)
        if card.column == "plan" and facts.plan_status == "draft" and facts.open_waits:
            raise WaitsForOpen(facts.open_waits)

    @contextmanager
    def review_starting(self, project_id: int, card_id: int) -> Iterator[None]:
        """One *Review* per card at a time, also while its criteria checks run before the review's row exists."""
        key = (project_id, card_id)
        with self.lock:
            if key in self.starting_reviews:
                raise ActionRunning("The card's review is starting.")
            self.starting_reviews.add(key)
        try:
            yield
        finally:
            with self.lock:
                self.starting_reviews.discard(key)

    def review(self, project_id: int, card_id: int) -> int:
        """*Review* and *Review again*. In a worker thread (the criteria checks run on the event loop)."""
        with self.review_starting(project_id, card_id):
            return self.route.review(project_id, card_id)

    def send_back(self, project_id: int, card_id: int, finding: int | None, criterion: int | None) -> CardDetail:
        """*Send back*: from Review with one finding or unmet criterion of the review (the route's), from Build when
        the build failed or was interrupted, with why it stopped. The card goes to Design to be planned again.
        Blocking."""
        card, facts = self.route.card_and_facts(project_id, card_id)
        if card.column != "build":
            return self.route.send_back(project_id, card_id, finding, criterion)
        if card.links.plan_id is None:
            raise NoPlan("The card has no build to send back from.")
        try:
            view = self.route.plans().get(card.links.plan_id)
        except PlanNotFound as error:
            raise NoPlan("The card's plan is gone.") from error
        reason = failed_build_reason(facts.plan_status, view.failure)
        detail = self.board.apply_event(project_id, card_id, "send_back", "user", reason=reason)
        return detail if detail is not None else self.board.get_card(project_id, card_id)
