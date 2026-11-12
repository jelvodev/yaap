"""A second opinion on one reply: start it, preview what it would cost, run it and store the report.

The reply decides what is checked: when it changed files, those changes are reviewed like a conversation's changes
(reviews/service.py); when it changed nothing, its answer is checked against the question and the project files it
names. The reviewer is another vendor's model, or after the person agreed to it the writer's own vendor's strongest
other model (reviews/reviewer.py). Nothing is applied and nothing is added to the conversation; the report is stored
as a review that belongs to the reply.
"""

import logging
from dataclasses import dataclass, field
from typing import Literal

import anyio
from sqlalchemy.orm import Session

from app.core.model_choice import ForUtility, ModelChoice, ModelChoiceError, read_key
from app.core.outgoing_checks import outgoing_rules, request_refusal
from app.core.reply_services import ReplyServices
from app.core.search_index import message_text
from app.core.utility import UtilityCall, ask_utility_model, utility_request
from app.files.service import project_files
from app.reviews.answer import AnswerReport, FileMaterial, answer_prompt, named_paths, parse_answer_report
from app.reviews.answer_files import read_named_files
from app.reviews.report import ReportError, ordered, plan_parts, split_files
from app.reviews.reply_scope import answer_scope, reply_scope
from app.reviews.reviewer import Estimate, ReviewerPlan, estimate_cost, second_opinion_reviewer
from app.reviews.scope import NothingToReview, ReviewScope
from app.reviews.service import (
    REVIEW_MAX_TOKENS,
    REVIEW_TASK,
    PreparedReview,
    ReviewOutcome,
    ReviewService,
    add_usage,
    prompts_for,
    spawn,
)
from app.secrets.service import mask
from app.storage.models import Conversation, Message, Project, Review
from app.targets.base import TargetError, TargetFiles

logger = logging.getLogger(__name__)

OpinionKind = Literal["changes", "answer"]


class SecondOpinionRefused(Exception):
    """No second opinion can be given now; the message says why."""


@dataclass(frozen=True)
class OpinionPlan:
    scope: ReviewScope
    reviewer: ReviewerPlan

    @property
    def kind(self) -> OpinionKind:
        return "changes" if self.scope.source == "reply" else "answer"


@dataclass(frozen=True)
class OpinionPreview:
    kind: OpinionKind
    available: bool
    reviewer_provider: str | None
    reviewer_model: str | None
    reviewer_label: str | None
    same_vendor: bool
    reason: str | None
    estimate: Estimate | None


@dataclass(frozen=True)
class PreparedAnswer:
    call: UtilityCall
    question: str | None
    answer: str
    paths: list[str]
    files: TargetFiles | None
    # Why the project's files cannot be read, when the answer names files but the project's target is unavailable.
    files_note: str | None


@dataclass
class AnswerOutcome(ReviewOutcome):
    report: AnswerReport | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SecondOpinions:
    reviews: ReviewService

    @property
    def services(self) -> ReplyServices:
        return self.reviews.services

    # --- Planning -------------------------------------------------------------------------------------------------

    def plan(self, session: Session, conversation: Conversation, message: Message) -> OpinionPlan:
        """What would be checked and by whom. Raises SecondOpinionRefused when there is nothing to check."""
        services = self.services
        try:
            scope = reply_scope(session, self.reviews.checkpoints, services.workspace_dir, conversation, message)
            scope = scope or answer_scope(session, conversation, message)
        except NothingToReview as error:
            raise SecondOpinionRefused(str(error)) from error
        request = ForUtility(conversation.preset, "utility", scope.project_id, REVIEW_TASK)
        reviewer = second_opinion_reviewer(
            session,
            services.providers,
            scope.environment_id,
            request,
            scope.writer_vendors,
            scope.writer_models,
        )
        return OpinionPlan(scope, reviewer)

    def preview_for_reply(self, conversation_id: int, message_id: int) -> OpinionPreview:
        """What a second opinion on the reply would be and cost. In a worker thread."""
        with self.services.session_factory() as session:
            conversation = session.get_one(Conversation, conversation_id)
            message = session.get_one(Message, message_id)
            plan = self.plan(session, conversation, message)
            choice = plan.reviewer.choice
            if choice is None:
                return OpinionPreview(plan.kind, False, None, None, None, False, plan.reviewer.reason, None)
            chars, requests = self.prompt_size(session, plan.scope, message)
            pricing = choice.info.pricing if choice.info is not None else None
            estimate = estimate_cost(chars, requests, REVIEW_MAX_TOKENS, pricing, choice.billing == "metered")
            label = choice.info.display_name if choice.info is not None else choice.model
            return OpinionPreview(
                plan.kind,
                True,
                choice.spec.name,
                choice.model,
                f"{label} ({choice.spec.display_name})",
                plan.reviewer.same_vendor,
                plan.reviewer.reason,
                estimate,
            )

    def prompt_size(self, session: Session, scope: ReviewScope, message: Message) -> tuple[int, int]:
        """The characters the requests would hold and how many requests there are."""
        if scope.source == "reply":
            project = session.get_one(Project, scope.project_id)
            rules = self.reviews.project_rules(session, project) or ""
            parts = plan_parts(split_files(scope.diff))
            prompts = prompts_for(scope.objective, rules, parts)
            return sum(len(prompt) for prompt in prompts), len(prompts)
        answer = message_text(message)
        project = session.get(Project, scope.project_id) if scope.project_id is not None else None
        files, _ = self.files_for(session, project)
        found = self.read_files(files, named_paths(answer))[0]
        return len(answer_prompt(scope.objective, answer, found)[0]), 1

    # --- Starting -------------------------------------------------------------------------------------------------

    def start_for_reply(self, conversation_id: int, message_id: int, allow_same_vendor: bool) -> int:
        """Store a running second opinion on the reply and run it in the background. In a worker thread; raises
        SecondOpinionRefused when it cannot be given or needs the same vendor and that is not allowed."""
        with self.services.session_factory() as session:
            conversation = session.get_one(Conversation, conversation_id)
            plan = self.plan(session, conversation, session.get_one(Message, message_id))
            choice = plan.reviewer.choice
            if choice is None:
                raise SecondOpinionRefused(plan.reviewer.reason or "No model can give a second opinion.")
            if plan.reviewer.same_vendor and not allow_same_vendor:
                raise SecondOpinionRefused(
                    f"{plan.reviewer.reason} Only a model of the same vendor can give it; confirm that to go on."
                )
            review_id = self.reviews.create(session, plan.scope)
            review = session.get_one(Review, review_id)
            review.reviewer_provider, review.reviewer_model = choice.spec.name, choice.model
            review.same_vendor = plan.reviewer.same_vendor
            session.commit()
        anyio.from_thread.run_sync(spawn, self.run(review_id, choice))
        return review_id

    # --- Running (on the event loop) ------------------------------------------------------------------------------

    async def run(self, review_id: int, choice: ModelChoice) -> None:
        reviews = self.reviews
        try:
            prepared = await anyio.to_thread.run_sync(self.prepare, review_id, choice)
            if prepared is None:
                return
            if isinstance(prepared, PreparedReview):
                outcome = await reviews.ask(prepared)
                await anyio.to_thread.run_sync(reviews.finish, review_id, outcome)
            else:
                answered = await self.ask_answer(prepared)
                await anyio.to_thread.run_sync(self.finish_answer, review_id, answered)
        except Exception:  # A background task: the second opinion ends as failed and the cause is logged.
            logger.exception("Second opinion %s failed.", review_id)
            await anyio.to_thread.run_sync(reviews.end, review_id, "failed", "The second opinion failed; see the log.")

    def prepare(self, review_id: int, choice: ModelChoice) -> PreparedReview | PreparedAnswer | None:
        """The key, the outgoing rules and what is asked; None when the second opinion ended here."""
        services = self.services
        with services.session_factory() as session:
            review = session.get_one(Review, review_id)
            try:
                api_key = read_key(session, services.cipher, services.credential_types, choice, review.conversation_id)
            except ModelChoiceError as error:
                self.reviews.end_in(session, review, "skipped", str(error))
                return None
            outgoing = outgoing_rules(session, services.cipher, review.environment_id, review.project_id)
            call = UtilityCall(choice, api_key, outgoing, REVIEW_TASK, review.project_id, review.conversation_id)
            if review.source == "reply":
                project = session.get_one(Project, review.project_id)
                rules = mask(self.reviews.project_rules(session, project) or "", outgoing.secrets)
                parts = plan_parts(split_files(review.diff))
                review.notes = [*review.notes, *parts.notes]
                session.commit()
                return PreparedReview(review_id, call, prompts_for(review.objective, rules, parts))
            message = session.get_one(Message, review.message_id)
            project = session.get(Project, review.project_id) if review.project_id is not None else None
            files, files_note = self.files_for(session, project)
            answer = mask(message_text(message), outgoing.secrets)
            return PreparedAnswer(call, review.objective, answer, named_paths(answer), files, files_note)

    def files_for(self, session: Session, project: Project | None) -> tuple[TargetFiles | None, str | None]:
        """The project's confined files, or why there are none (no project is no reason to mention)."""
        if project is None:
            return None, None
        try:
            services = self.services
            return project_files(session, services.cipher, services.workspace_dir, project).files, None
        except TargetError as error:
            return None, f"The project's files could not be read: {error}"

    @staticmethod
    def read_files(files: TargetFiles | None, paths: list[str]) -> tuple[list[FileMaterial], list[str]]:
        """In a worker thread: the readable named files and the notes on those left out."""
        if files is None or not paths:
            return [], []
        return anyio.from_thread.run(read_named_files, files, paths)

    async def ask_answer(self, prepared: PreparedAnswer) -> AnswerOutcome:
        outcome = AnswerOutcome()
        call = prepared.call
        found, notes = (
            await read_named_files(prepared.files, prepared.paths) if prepared.files is not None else ([], [])
        )
        masked = [FileMaterial(file.path, mask(file.text, call.outgoing.secrets)) for file in found]
        prompt, shortened = answer_prompt(prepared.question, prepared.answer, masked)
        outcome.notes = [*([prepared.files_note] if prepared.files_note else []), *notes, *shortened]
        answer = await ask_utility_model(self.services.utility, call, prompt, REVIEW_MAX_TOKENS)
        if answer is None:
            refusal = request_refusal(utility_request(call.choice, prompt, REVIEW_MAX_TOKENS), call.outgoing)
            outcome.problem = refusal or f"The reviewer model {call.choice.model} did not answer."
            return outcome
        add_usage(outcome, answer)
        try:
            outcome.report = parse_answer_report(answer.text)
        except ReportError as error:
            outcome.problem = str(error)
        return outcome

    def finish_answer(self, review_id: int, outcome: AnswerOutcome) -> None:
        with self.services.session_factory() as session:
            review = session.get_one(Review, review_id)
            report = outcome.report
            review.notes = [*review.notes, *outcome.notes]
            if report is not None:
                review.summary = report.summary or None
                review.agrees, review.alternative = report.agrees, report.alternative
                review.findings = [point.stored() for point in ordered(report.points)]
            review.cost, review.input_tokens = outcome.cost, outcome.input_tokens
            review.output_tokens = outcome.output_tokens
            self.reviews.end_in(session, review, "failed" if outcome.problem else "done", outcome.problem)
