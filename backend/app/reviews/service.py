"""Reviews by another vendor: start, run and store a review.

A review is stored as `running` the moment it starts, with the diff it looks at, so the changes view and the
conversation can show it; the model call runs in the background. The reviewer is chosen by other_vendor_choice():
the review task's own utility model when it is of none of the vendors that wrote the changes, else the
cheapest allowed catalog model of another vendor. When the policy, the connections, the keys or the budget allow
none, the review ends as `skipped` with the reason. The request goes through the utility model's path, so the audit
log, the outgoing check and the usage records (task `review`) see it like every other model call. Secret values in
the diff and the rules are replaced by placeholders before anything is stored or sent. Nothing is ever applied.
"""

import logging
import threading
from collections.abc import Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

import anyio
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.blueprint.context import standards_part
from app.blueprint.layers import active_items
from app.blueprint.levels import layers_for
from app.checkpoints.service import CheckpointStore
from app.core.background import BackgroundWork
from app.core.goals import OPEN_STATUSES, latest_goal
from app.core.model_choice import ForUtility, ModelChoiceError, read_key
from app.core.other_vendor import other_vendor_choice
from app.core.outgoing_checks import outgoing_rules, request_refusal
from app.core.reply_services import ReplyServices
from app.core.utility import UtilityAnswer, UtilityCall, ask_utility_model, utility_request
from app.git.service import MAX_DIFF_CHARS
from app.reviews.report import (
    Finding,
    ReportError,
    ReviewParts,
    ordered,
    parse_report,
    plan_parts,
    review_prompt,
    split_files,
)
from app.reviews.scope import ReviewScope, changes_scope, conversation_scope, goal_scope
from app.secrets.service import mask, resolve_secrets
from app.storage.models import Conversation, Environment, Goal, Project, Review

logger = logging.getLogger(__name__)

ReviewStatus = Literal["running", "done", "skipped", "failed"]
REVIEW_TASK = "review"
REVIEW_MAX_TOKENS = 4000
# Reviews run on their own.
REVIEWS: BackgroundWork[None] = BackgroundWork()
# Two entry points can see the same goal end (the next-turn check and the scheduled run's end).
GOAL_LOCK = threading.Lock()


@dataclass(frozen=True)
class PreparedReview:
    """A review whose reviewer, key, prompts and outgoing rules are settled."""

    review_id: int
    call: UtilityCall
    prompts: tuple[str, ...]


@dataclass
class ReviewOutcome:
    summaries: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    cost: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    problem: str | None = None


def spawn(work: Coroutine[Any, Any, None]) -> None:
    """Run a review on the event loop by itself. On the event loop."""
    REVIEWS.start(work)


@dataclass(frozen=True)
class ReviewService:
    services: ReplyServices

    # --- Starting -------------------------------------------------------------------------------------------------

    def start_for_changes(self, project_id: int) -> int:
        """Review the project's uncommitted changes. In a worker thread; raises NothingToReview."""
        with self.services.session_factory() as session:
            scope = changes_scope(session, self.services.workspace_dir, session.get_one(Project, project_id))
            review_id = self.create(session, scope)
        anyio.from_thread.run_sync(spawn, self.run(review_id))
        return review_id

    def start_for_conversation(self, conversation_id: int) -> int:
        """Review what a conversation changed. In a worker thread; raises NothingToReview."""
        with self.services.session_factory() as session:
            conversation = session.get_one(Conversation, conversation_id)
            scope = conversation_scope(session, self.checkpoints, self.services.workspace_dir, conversation)
            review_id = self.create(session, scope)
        anyio.from_thread.run_sync(spawn, self.run(review_id))
        return review_id

    def after_goal_from_thread(self, goal_id: int) -> None:
        """after_goal in the background, from a worker thread (the scheduler's)."""
        if self.services.reviews_after_goals:
            anyio.from_thread.run_sync(spawn, self.after_goal(goal_id))

    async def after_goal_of(self, conversation_id: int) -> None:
        """Review the conversation's latest goal when it has ended. On the event loop."""
        if not self.services.reviews_after_goals:
            return
        with self.services.session_factory() as session:
            goal = latest_goal(session, conversation_id)
            goal_id = goal.id if goal is not None else None
        if goal_id is not None:
            await self.after_goal(goal_id)

    async def after_goal(self, goal_id: int) -> None:
        """Review an ended goal that changed code, once. On the event loop."""
        review_id = await anyio.to_thread.run_sync(self.create_for_goal, goal_id)
        if review_id is not None:
            await self.run(review_id)

    def create_for_goal(self, goal_id: int) -> int | None:
        with GOAL_LOCK, self.services.session_factory() as session:
            goal = session.get(Goal, goal_id)
            if goal is None or goal.status in OPEN_STATUSES:
                return None
            if session.scalar(select(Review.id).where(Review.goal_id == goal_id)) is not None:
                return None
            scope = goal_scope(session, self.checkpoints, self.services.workspace_dir, goal)
            return self.create(session, scope) if scope is not None else None

    @property
    def checkpoints(self) -> CheckpointStore:
        return CheckpointStore(self.services.session_factory, self.services.data_dir)

    def create(self, session: Session, scope: ReviewScope) -> int:
        """Store the review as running, its diff with secrets as placeholders."""
        secrets = resolve_secrets(session, self.services.cipher, scope.environment_id, scope.project_id)
        diff = mask(scope.diff, secrets)
        notes = []
        if len(diff) > MAX_DIFF_CHARS:
            diff = diff[:MAX_DIFF_CHARS]
            notes.append(f"The diff is longer than {MAX_DIFF_CHARS:,} characters; the rest is not reviewed.")
        review = Review(
            environment_id=scope.environment_id,
            project_id=scope.project_id,
            conversation_id=scope.conversation_id,
            goal_id=scope.goal_id,
            message_id=scope.message_id,
            source=scope.source,
            status="running",
            objective=mask(scope.objective, secrets) if scope.objective else None,
            writer_vendors=list(scope.writer_vendors),
            diff=diff,
            notes=notes,
            findings=[],
            created_at=datetime.now(UTC),
        )
        session.add(review)
        session.commit()
        return review.id

    # --- Running (on the event loop) ------------------------------------------------------------------------------

    async def run(self, review_id: int) -> None:
        try:
            prepared = await anyio.to_thread.run_sync(self.prepare, review_id)
            if prepared is None:
                return
            outcome = await self.ask(prepared)
            await anyio.to_thread.run_sync(self.finish, review_id, outcome)
        except Exception:  # A background task: the review ends as failed and the cause is logged.
            logger.exception("Review %s failed.", review_id)
            await anyio.to_thread.run_sync(self.end, review_id, "failed", "The review failed; see the log.")

    def prepare(self, review_id: int) -> PreparedReview | None:
        """The reviewer and the prompts; None when the review ended here (skipped, or the key cannot be used)."""
        services = self.services
        with services.session_factory() as session:
            review = session.get_one(Review, review_id)
            project = session.get_one(Project, review.project_id)
            conversation = session.get(Conversation, review.conversation_id) if review.conversation_id else None
            request = ForUtility(conversation.preset if conversation else None, "utility", project.id, REVIEW_TASK)
            vendors = set(review.writer_vendors)
            try:
                choice = other_vendor_choice(session, services.providers, review.environment_id, request, vendors)
                api_key = read_key(session, services.cipher, services.credential_types, choice)
            except ModelChoiceError as error:
                self.end_in(session, review, "skipped", str(error))
                return None
            review.reviewer_provider, review.reviewer_model = choice.spec.name, choice.model
            outgoing = outgoing_rules(session, services.cipher, review.environment_id, project.id)
            rules = mask(self.project_rules(session, project) or "", outgoing.secrets)
            parts = plan_parts(split_files(review.diff))
            review.notes = [*review.notes, *parts.notes]
            session.commit()
            call = UtilityCall(choice, api_key, outgoing, REVIEW_TASK, project.id, review.conversation_id)
            return PreparedReview(review_id, call, prompts_for(review.objective, rules, parts))

    def project_rules(self, session: Session, project: Project) -> str | None:
        environment = session.get_one(Environment, project.environment_id)
        layers = layers_for(self.services.blueprint_dir, self.services.workspace_dir, environment, project)
        return standards_part(active_items(layers))

    async def ask(self, prepared: PreparedReview) -> ReviewOutcome:
        outcome = ReviewOutcome()
        context, call = self.services.utility, prepared.call
        for prompt in prepared.prompts:
            answer = await ask_utility_model(context, call, prompt, REVIEW_MAX_TOKENS)
            if answer is None:
                refusal = request_refusal(utility_request(call.choice, prompt, REVIEW_MAX_TOKENS), call.outgoing)
                outcome.problem = refusal or f"The reviewer model {call.choice.model} did not answer."
                return outcome
            add_usage(outcome, answer)
            try:
                summary, findings = parse_report(answer.text)
            except ReportError as error:
                outcome.problem = str(error)
                return outcome
            if summary:
                outcome.summaries.append(summary)
            outcome.findings.extend(findings)
        return outcome

    def finish(self, review_id: int, outcome: ReviewOutcome) -> None:
        with self.services.session_factory() as session:
            review = session.get_one(Review, review_id)
            review.summary = "\n\n".join(outcome.summaries) or None
            review.findings = [finding.stored() for finding in ordered(outcome.findings)]
            review.cost, review.input_tokens, review.output_tokens = (
                outcome.cost,
                outcome.input_tokens,
                outcome.output_tokens,
            )
            self.end_in(session, review, "failed" if outcome.problem else "done", outcome.problem)

    def end(self, review_id: int, status: ReviewStatus, reason: str | None) -> None:
        with self.services.session_factory() as session:
            review = session.get(Review, review_id)
            if review is not None:
                self.end_in(session, review, status, reason)

    @staticmethod
    def end_in(session: Session, review: Review, status: ReviewStatus, reason: str | None) -> None:
        review.status, review.reason, review.finished_at = status, reason, datetime.now(UTC)
        session.commit()


def prompts_for(objective: str | None, rules: str, parts: ReviewParts) -> tuple[str, ...]:
    count = len(parts.parts)
    return tuple(review_prompt(objective, rules, files, number, count) for number, files in enumerate(parts.parts, 1))


def add_usage(outcome: ReviewOutcome, answer: UtilityAnswer) -> None:
    if answer.cost is not None:
        outcome.cost = (outcome.cost or 0.0) + answer.cost
    if answer.completed is not None:
        usage = answer.completed.usage
        outcome.input_tokens += usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens
        outcome.output_tokens += usage.output_tokens
