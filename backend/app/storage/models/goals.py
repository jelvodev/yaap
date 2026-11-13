"""Queued messages, goals and reviews."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.conversation_types import GoalStatus
from app.storage.models.base import Base, UtcDateTime, utc_now


class QueuedMessage(Base):
    """A message written while a reply ran, sent when it ends."""

    __tablename__ = "queued_messages"
    # Ids are never reused: an eval run remembers the id of the queue item it came from.
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    attachment_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    position: Mapped[int] = mapped_column(Integer)
    # A send-now request is tied to one running reply; the row stays queued until its message is accepted.
    send_now_job_id: Mapped[str | None] = mapped_column(String(36), index=True)
    # A steering row remains queued until a tool result hands its text to this run.
    steering_run_id: Mapped[int | None] = mapped_column(Integer, index=True)
    steering_claimed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)


class Goal(Base):
    """An objective a project conversation works towards over several turns."""

    __tablename__ = "goals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    objective: Mapped[str] = mapped_column(Text)
    # active, paused, done, blocked, limit (turns or cost used up) or stopped (by the user or a failed reply)
    status: Mapped[GoalStatus] = mapped_column(String(20))
    max_turns: Mapped[int] = mapped_column(Integer)
    # In US dollars, counting charged usage only; 0 means no cost limit.
    max_cost: Mapped[float] = mapped_column(Float)
    turns: Mapped[int] = mapped_column(Integer)
    summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime())


class Review(Base):
    """A review of a project's changes by a model of another vendor than the one that wrote them (app/reviews).
    Nothing of it is applied; the findings are a report beside the diff it reviewed."""

    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    environment_id: Mapped[int] = mapped_column(ForeignKey("environments.id"))
    # Empty for the check of an answer in a conversation without a project.
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id", ondelete="SET NULL"), index=True)
    # The reply a second opinion is about.
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    # The goal (or scheduled task run's goal) whose end started it; one review per goal.
    goal_id: Mapped[int | None] = mapped_column(Integer, index=True)
    # goal, task, changes (the project's uncommitted changes), conversation, reply (the changes of one reply) or
    # answer (a check of a reply that changed nothing)
    source: Mapped[str] = mapped_column(String(20))
    # running, done, skipped (no allowed model of another vendor, or nothing to review) or failed
    status: Mapped[str] = mapped_column(String(20))
    # Why it was skipped or failed.
    reason: Mapped[str | None] = mapped_column(Text)
    objective: Mapped[str | None] = mapped_column(Text)
    # The vendors (provider names) whose models wrote the changes.
    writer_vendors: Mapped[list[str]] = mapped_column(JSON, default=list)
    reviewer_provider: Mapped[str | None] = mapped_column(String(40))
    reviewer_model: Mapped[str | None] = mapped_column(String(80))
    # The reviewer is of the writer's own vendor, as no other vendor was allowed.
    same_vendor: Mapped[bool] = mapped_column(Boolean, default=False)
    # An answer check: whether the reviewer agrees (empty when it does not say) and what it would answer instead.
    agrees: Mapped[bool | None] = mapped_column(Boolean)
    alternative: Mapped[str | None] = mapped_column(Text)
    # The diff as reviewed, secrets as placeholders.
    diff: Mapped[str] = mapped_column(Text, default="")
    # What was shortened or left out of the review.
    notes: Mapped[list[str]] = mapped_column(JSON, default=list)
    summary: Mapped[str | None] = mapped_column(Text)
    # Each finding: path, line (or null), severity (high, medium or low) and explanation.
    findings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    cost: Mapped[float | None] = mapped_column(Float)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    # A build's review: the diff base_commit..branch in the project repository, and the card it belongs to (cards
    # live in git, so no foreign key).
    base_commit: Mapped[str | None] = mapped_column(String(40))
    branch: Mapped[str | None] = mapped_column(String(255))
    card_id: Mapped[int | None] = mapped_column(Integer)
    # What YAAP checked per acceptance criterion and where it is shown.
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON)
