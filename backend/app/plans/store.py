"""Reading and writing plan rows. Status changes compare the old status in the same UPDATE, so two changes at once
cannot both win."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import CursorResult, select, update
from sqlalchemy.orm import Session

from app.plans.model import Plan, PlanStatus, plan_from_json, plan_to_json
from app.plans.progress import BuildInfo, build_info_to_json
from app.plans.models import PlanRecord


@dataclass(frozen=True)
class PlanStore:
    """The `plans` rows, within the caller's session; the caller commits."""

    session: Session

    def create(self, project_id: int, conversation_id: int, plan: Plan, status: PlanStatus) -> PlanRecord:
        record = PlanRecord(
            project_id=project_id,
            conversation_id=conversation_id,
            status=status,
            title=plan.title,
            body=plan_to_json(plan),
            created_at=datetime.now(UTC),
        )
        self.session.add(record)
        self.session.flush()
        return record

    def get(self, plan_id: int) -> PlanRecord | None:
        return self.session.get(PlanRecord, plan_id)

    def list_for_conversation(self, conversation_id: int, status: PlanStatus | None = None) -> list[PlanRecord]:
        query = select(PlanRecord).where(PlanRecord.conversation_id == conversation_id)
        if status is not None:
            query = query.where(PlanRecord.status == status)
        return list(self.session.scalars(query.order_by(PlanRecord.id)))

    def update_body(self, record: PlanRecord, plan: Plan) -> None:
        record.title = plan.title
        record.body = plan_to_json(plan)

    def set_status(self, plan_id: int, old: PlanStatus, new: PlanStatus) -> bool:
        """Move the plan from `old` to `new`; False when it was no longer in `old`."""
        values: dict[str, object] = {"status": new}
        if new == "approved":
            values["approved_at"] = datetime.now(UTC)
        if new in ("done", "failed", "cancelled"):
            values["finished_at"] = datetime.now(UTC)
        result = self.session.execute(
            update(PlanRecord)
            .where(PlanRecord.id == plan_id, PlanRecord.status == old)
            .values(**values)
            .execution_options(synchronize_session="fetch")
        )
        return isinstance(result, CursorResult) and result.rowcount == 1

    def plans_by_id(self, plan_ids: Iterable[int]) -> dict[int, Plan]:
        """The plans with these ids, by id; ids without a row are left out."""
        wanted = set(plan_ids)
        if not wanted:
            return {}
        rows = self.session.scalars(select(PlanRecord).where(PlanRecord.id.in_(wanted)))
        return {row.id: plan_from_json(row.body) for row in rows}

    def in_project(self, project_id: int, statuses: Iterable[PlanStatus]) -> list[PlanRecord]:
        """The project's plans in these statuses, the earliest approved first."""
        query = select(PlanRecord).where(PlanRecord.project_id == project_id, PlanRecord.status.in_(list(statuses)))
        return list(self.session.scalars(query.order_by(PlanRecord.approved_at, PlanRecord.id)))

    def with_status(self, statuses: Iterable[PlanStatus]) -> list[PlanRecord]:
        return list(self.session.scalars(select(PlanRecord).where(PlanRecord.status.in_(list(statuses)))))

    def save_progress(self, record: PlanRecord, plan: Plan, build: BuildInfo) -> None:
        """The plan with its steps' state and the build's own facts (body["build"])."""
        record.body = {**plan_to_json(plan), "build": build_info_to_json(build)}
