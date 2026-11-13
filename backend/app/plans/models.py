"""The `plans` table. It lives beside the plan's code, on the shared declarative base; the migrations' environment
imports it so the schema check sees it."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.plans.model import PlanStatus
from app.storage.models import Base, UtcDateTime, utc_now


class PlanRecord(Base):
    """A plan of a project conversation: its steps with their state in `body`, and, once it is built, where."""

    __tablename__ = "plans"
    __table_args__ = (Index("ix_plans_project_id_status", "project_id", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    # The card the plan belongs to; cards live in git, so there is no foreign key.
    card_id: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[PlanStatus] = mapped_column(String(12))
    title: Mapped[str] = mapped_column(String(200))
    # The plan (app.plans.model.plan_to_json) with each step's state.
    body: Mapped[dict[str, Any]] = mapped_column(JSON)
    # Where the build starts and works: the project's branch and commit, the build's branch, and its folder relative
    # to the project folder.
    base_branch: Mapped[str | None] = mapped_column(String(120))
    base_commit: Mapped[str | None] = mapped_column(String(40))
    build_branch: Mapped[str | None] = mapped_column(String(120))
    build_folder: Mapped[str | None] = mapped_column(String(300))
    # The build's own run (agent_runs), whose sub-runs are the steps.
    build_run_id: Mapped[int | None] = mapped_column(Integer)
    failure: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)
    approved_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
