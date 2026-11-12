"""The plan service: what the agent's `submit_plan`, the chat's plan card, the API and a build do with a plan.

Every status change goes through `next_status`; a change it does not allow is a `PlanConflict` (409 in the API).
Plan text comes from a model or a hand edit: it is stored as data and shown as such, never followed as instructions.
"""

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.plans.model import Plan, PlanEvent, PlanStatus, next_status, plan_from_json, plan_warnings, validate_plan
from app.plans.models import PlanRecord
from app.plans.progress import BuildInfo, build_info_from_json, reopened
from app.plans.store import PlanStore
from app.storage.models import UsageRecord

logger = logging.getLogger(__name__)

BuildRefusal = Literal[
    "plan_not_draft", "no_repository", "build_running", "copy_failed", "not_resumable", "build_unavailable"
]
MAX_FAILURE = 1000


class PlanNotFound(Exception):
    """No plan with that id."""


class PlanConflict(Exception):
    """The plan's status does not allow this; the message is safe to show."""


class InvalidPlan(Exception):
    """The plan cannot be used; `errors` lists every reason, each naming its step."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


class BuildRefused(Exception):
    """A build cannot start or go on; `code` names why for the API and the message is safe to show."""

    def __init__(self, code: BuildRefusal, message: str) -> None:
        super().__init__(message)
        self.code: BuildRefusal = code


# Hears a plan's events (plan id, event): the current-state refresh and the card's route. A build's events come on the
# event loop; `submit` and `approve` come from a worker thread, so a listener that starts work for them hands it to the
# event loop itself.
PlanListener = Callable[[int, PlanEvent], None]


@dataclass
class PlanListeners:
    """The listeners of plan events, so plans/ imports nothing of the features that react to a build."""

    listeners: list[PlanListener] = field(default_factory=list)

    def add(self, listener: PlanListener) -> None:
        self.listeners.append(listener)

    def tell(self, plan_id: int, event: PlanEvent) -> None:
        for listener in self.listeners:
            try:
                listener(plan_id, event)
            except Exception:  # noqa: BLE001 - one listener's failure must not end a build or reach the others
                logger.warning("A listener of plan %s failed on %s.", plan_id, event, exc_info=True)


@dataclass(frozen=True)
class BuildStart:
    """Where an approved plan's build starts and works."""

    base_branch: str | None
    base_commit: str
    build_branch: str
    build_folder: str
    uncommitted: int


@dataclass(frozen=True)
class PlanView:
    id: int
    project_id: int
    conversation_id: int
    card_id: int | None
    status: PlanStatus
    plan: Plan
    warnings: list[str]
    failure: str | None
    created_at: datetime
    approved_at: datetime | None
    finished_at: datetime | None
    base_branch: str | None = None
    base_commit: str | None = None
    build_branch: str | None = None
    build_folder: str | None = None
    build_run_id: int | None = None
    build: BuildInfo = field(default_factory=BuildInfo)


def plan_view(record: PlanRecord) -> PlanView:
    plan = plan_from_json(record.body)
    return PlanView(
        id=record.id,
        project_id=record.project_id,
        conversation_id=record.conversation_id,
        card_id=record.card_id,
        status=record.status,
        plan=plan,
        warnings=plan_warnings(plan),
        failure=record.failure,
        created_at=record.created_at,
        approved_at=record.approved_at,
        finished_at=record.finished_at,
        base_branch=record.base_branch,
        base_commit=record.base_commit,
        build_branch=record.build_branch,
        build_folder=record.build_folder,
        build_run_id=record.build_run_id,
        build=build_info_from_json(record.body),
    )


def checked_plan(arguments: Mapping[str, Any]) -> Plan:
    checked = validate_plan(arguments)
    if isinstance(checked, list):
        raise InvalidPlan(checked)
    return checked


def move(store: PlanStore, record: PlanRecord, event: PlanEvent) -> None:
    """Apply `event` to the plan's status, or raise PlanConflict."""
    new = next_status(record.status, event)
    if new is None or not store.set_status(record.id, record.status, new):
        raise PlanConflict(f"Plan {record.id} is {record.status}; it cannot be changed that way now.")


@dataclass(frozen=True)
class PlanService:
    session_factory: sessionmaker[Session]
    # Told when a plan is submitted; None where nothing listens (the API, a build's own reads).
    listeners: PlanListeners | None = None

    def submit(self, project_id: int, conversation_id: int, arguments: Mapping[str, Any]) -> PlanView:
        """Store a new draft plan; the conversation's other drafts are replaced by it in the same transaction."""
        plan = checked_plan(arguments)
        status = next_status(None, "submit")
        if status is None:
            raise PlanConflict("A plan cannot be submitted.")
        with self.session_factory() as session:
            store = PlanStore(session)
            for earlier in store.list_for_conversation(conversation_id, "draft"):
                move(store, earlier, "replan")
            record = store.create(project_id, conversation_id, plan, status)
            session.commit()
            view = plan_view(record)
        if self.listeners is not None:
            self.listeners.tell(view.id, "submit")
        return view

    def get(self, plan_id: int) -> PlanView:
        with self.session_factory() as session:
            return plan_view(self.found(PlanStore(session), plan_id))

    def edit(self, plan_id: int, arguments: Mapping[str, Any]) -> PlanView:
        """Replace a draft's title, summary, risks and whole step list."""
        plan = checked_plan(arguments)
        with self.session_factory() as session:
            store = PlanStore(session)
            record = self.found(store, plan_id)
            move(store, record, "edit")
            store.update_body(record, plan)
            session.commit()
            session.refresh(record)
            return plan_view(record)

    def discard(self, plan_id: int) -> PlanView:
        with self.session_factory() as session:
            store = PlanStore(session)
            record = self.found(store, plan_id)
            move(store, record, "discard")
            session.commit()
            session.refresh(record)
            return plan_view(record)

    @staticmethod
    def found(store: PlanStore, plan_id: int) -> PlanRecord:
        record = store.get(plan_id)
        if record is None:
            raise PlanNotFound(f"Plan {plan_id} does not exist.")
        return record

    # --- Building: each change in one transaction, with the compare of the old status --------------------------

    def approve_for_build(self, plan_id: int, start: BuildStart) -> PlanView:
        """A draft becomes the project's one building plan, with where it builds. Call it under the build lock."""
        with self.session_factory() as session:
            store = PlanStore(session)
            record = self.found(store, plan_id)
            if record.status != "draft":
                message = f"Plan {plan_id} is {record.status}; only a draft can be approved."
                raise BuildRefused("plan_not_draft", message)
            refuse_second_build(store, record)
            checked_plan(record.body)
            move(store, record, "approve")
            move(store, record, "build_started")
            record.base_branch, record.base_commit = start.base_branch, start.base_commit
            record.build_branch, record.build_folder = start.build_branch, start.build_folder
            record.failure = None
            store.save_progress(record, plan_from_json(record.body), BuildInfo(uncommitted=start.uncommitted))
            session.commit()
            session.refresh(record)
            return plan_view(record)

    def continue_build(self, plan_id: int) -> PlanView:
        """An interrupted or failed build goes on from its first open step. Call it under the build lock."""
        with self.session_factory() as session:
            store = PlanStore(session)
            record = self.found(store, plan_id)
            if record.status not in ("interrupted", "failed") or not record.build_folder or not record.build_branch:
                message = f"Plan {plan_id} is {record.status}; there is no build to go on with."
                raise BuildRefused("not_resumable", message)
            refuse_second_build(store, record)
            move(store, record, "continue")
            store.save_progress(record, reopened(plan_from_json(record.body)), build_info_from_json(record.body))
            record.failure, record.finished_at = None, None
            session.commit()
            session.refresh(record)
            return plan_view(record)

    def build_cost(self, view: PlanView) -> float | None:
        """What the build's steps cost: the charged usage of the workers and takeovers in the plan's conversation
        while it was built. None before it was approved."""
        if view.approved_at is None:
            return None
        query = select(func.coalesce(func.sum(UsageRecord.cost), 0.0)).where(
            UsageRecord.conversation_id == view.conversation_id,
            UsageRecord.purpose == "worker",
            UsageRecord.created_at >= view.approved_at,
        )
        if view.finished_at is not None:
            query = query.where(UsageRecord.created_at <= view.finished_at)
        with self.session_factory() as session:
            return float(session.scalar(query) or 0.0)

    def refuse_second_build(self, project_id: int, plan_id: int) -> None:
        """Raise BuildRefused when another plan of the project is being built."""
        with self.session_factory() as session:
            other = PlanStore(session).building_in_project(project_id, plan_id)
        if other is not None:
            raise BuildRefused("build_running", f"Plan {other.id} is already being built in this project.")

    def save_progress(self, plan_id: int, plan: Plan, info: BuildInfo) -> None:
        with self.session_factory() as session:
            store = PlanStore(session)
            store.save_progress(self.found(store, plan_id), plan, info)
            session.commit()

    def set_build_run(self, plan_id: int, run_id: int | None) -> None:
        with self.session_factory() as session:
            self.found(PlanStore(session), plan_id).build_run_id = run_id
            session.commit()

    def end_build(self, plan_id: int, event: PlanEvent, failure: str | None = None) -> PlanView:
        """The build ends: done, failed or stopped (`event`), with the reason when it did not succeed."""
        with self.session_factory() as session:
            store = PlanStore(session)
            record = self.found(store, plan_id)
            move(store, record, event)
            record.failure = failure[:MAX_FAILURE] if failure else None
            session.commit()
            session.refresh(record)
            return plan_view(record)

    def build_gone(self, plan_id: int) -> None:
        """The copy was removed: the plan no longer points at it."""
        with self.session_factory() as session:
            record = PlanStore(session).get(plan_id)
            if record is not None:
                record.build_folder = None
                session.commit()


def refuse_second_build(store: PlanStore, record: PlanRecord) -> None:
    other = store.building_in_project(record.project_id, record.id)
    if other is not None:
        raise BuildRefused("build_running", f"Plan {other.id} is already being built in this project.")
