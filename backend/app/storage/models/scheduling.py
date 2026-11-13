"""Scheduled tasks and their runs."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.models.base import Base, UtcDateTime, utc_now


class ScheduledTask(Base):
    """Agent work of a project that runs by itself: on a cron schedule, at set moments or on demand.
    A run is a goal-mode run without the user: tools that would ask are refused unless the task allows them."""

    __tablename__ = "scheduled_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    prompt: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(80))
    effort: Mapped[str] = mapped_column(String(20))
    agent: Mapped[str | None] = mapped_column(String(80))
    preset: Mapped[str | None] = mapped_column(String(40))
    # What a run does: prompt (a goal-mode run of the prompt) or eval (an eval run of the project's cases).
    action: Mapped[str] = mapped_column(String(20), default="prompt")
    # For action eval: only this case; empty is every case.
    eval_case: Mapped[str | None] = mapped_column(String(60))
    # For action board_trigger: its settings (app.board.triggers.TriggerConfig).
    config: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # cron, moments or manual
    schedule_kind: Mapped[str] = mapped_column(String(20))
    cron: Mapped[str] = mapped_column(String(120), default="")
    timezone: Mapped[str] = mapped_column(String(60), default="UTC")
    # ISO date-times with their offset, for schedule_kind moments.
    moments: Mapped[list[str]] = mapped_column(JSON, default=list)
    max_turns: Mapped[int] = mapped_column(Integer)
    max_cost: Mapped[float] = mapped_column(Float)
    # What runs without asking in this task's runs, on top of the project's allowlists.
    allowed_commands: Mapped[list[str]] = mapped_column(JSON, default=list)
    allowed_domains: Mapped[list[str]] = mapped_column(JSON, default=list)
    # new: a new conversation per run; one: every run continues the same conversation.
    conversation_mode: Mapped[str] = mapped_column(String(10))
    conversation_id: Mapped[int | None] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Set while a run of the task goes on; claiming a run sets it in one update, so runs never overlap.
    running: Mapped[bool] = mapped_column(Boolean, default=False)
    next_run_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)


class ScheduledRun(Base):
    __tablename__ = "scheduled_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("scheduled_tasks.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[int | None] = mapped_column(Integer, index=True)
    goal_id: Mapped[int | None] = mapped_column(Integer)
    # schedule, missed (a run missed while YAAP was down) or manual
    trigger: Mapped[str] = mapped_column(String(20))
    # running, done, blocked, limit, stopped, paused or failed
    status: Mapped[str] = mapped_column(String(20))
    summary: Mapped[str | None] = mapped_column(Text)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    started_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
