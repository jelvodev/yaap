"""The plan service: what the agent's `submit_plan`, the chat's plan card and the API do with a plan.

Every status change goes through `next_status`; a change it does not allow is a `PlanConflict` (409 in the API).
Plan text comes from a model or a hand edit: it is stored as data and shown as such, never followed as instructions.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.plans.model import Plan, PlanEvent, PlanStatus, next_status, plan_from_json, plan_warnings, validate_plan
from app.plans.models import PlanRecord
from app.plans.store import PlanStore


class PlanNotFound(Exception):
    """No plan with that id."""


class PlanConflict(Exception):
    """The plan's status does not allow this; the message is safe to show."""


class InvalidPlan(Exception):
    """The plan cannot be used; `errors` lists every reason, each naming its step."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


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
            return plan_view(record)

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
