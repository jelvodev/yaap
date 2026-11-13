"""Conversations, messages, usage, attachments and model presets."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.conversation_types import MessageRole, MessageStatus
from app.storage.models.base import Base, UtcDateTime, utc_now


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    environment_id: Mapped[int] = mapped_column(ForeignKey("environments.id"), index=True)
    # Empty for a conversation outside a project.
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(80))
    effort: Mapped[str] = mapped_column(String(20))
    # The agent definition (by name) this conversation works as; empty for plain YAAP.
    agent: Mapped[str | None] = mapped_column(String(80))
    # Skills whose full text is part of this conversation, by name.
    pinned_skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    head_message_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)
    # Set while the conversation is archived: hidden from the sidebar, still searchable and readable.
    archived_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    # The preset the conversation started with, by name; its worker and utility models serve this conversation.
    preset: Mapped[str | None] = mapped_column(String(40))
    # When the utility model last read this conversation for memory suggestions.
    memory_checked_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    # When the utility model last read this conversation's signals for learning proposals.
    learning_checked_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    # The scheduled task that made it, and whether a finished task run in it is still unseen.
    scheduled_task_id: Mapped[int | None] = mapped_column(Integer)
    unread: Mapped[bool] = mapped_column(Boolean, default=False)
    # What the conversation may do without asking, chosen in the chat: default, full or read_only.
    access: Mapped[str] = mapped_column(String(20), default="default")
    # Outside a project: the machine the conversation works on; None is YAAP's container.
    target_id: Mapped[int | None] = mapped_column(ForeignKey("targets.id", ondelete="SET NULL"))
    # The context limit in tokens chosen for this conversation; None follows the context_limit setting.
    context_limit: Mapped[int | None] = mapped_column(Integer)
    # An imported Claude Code or Codex session: the tool, the file and what it held; None otherwise.
    imported_from: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # The conversation this one continues from a summary; None otherwise.
    continued_from_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), index=True
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id"))
    role: Mapped[MessageRole] = mapped_column(String(20))
    content: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    provider_raw: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    status: Mapped[MessageStatus] = mapped_column(String(20), default="complete")
    stop_reason: Mapped[str | None] = mapped_column(String(40))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)
    # A reply whose preset routes: the model that answered, the kind of message and why (core/routing.py).
    route: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class UsageRecord(Base):
    __tablename__ = "usage_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    environment_id: Mapped[int] = mapped_column(ForeignKey("environments.id"), index=True)
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id"))
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id"))
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(80))
    purpose: Mapped[str] = mapped_column(String(20), default="main")
    # Utility calls only: the task served (title, commit_message, import, …) and a short result for the chat.
    task: Mapped[str | None] = mapped_column(String(40))
    summary: Mapped[str | None] = mapped_column(String(300))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float | None] = mapped_column(Float)
    # "metered" (paid per token) or "subscription" (covered by a subscription, so cost stays empty).
    billing: Mapped[str] = mapped_column(String(20), default="metered")
    # Context size of the last model call of this reply, when it differs from the usage total (agent runs).
    context_tokens: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)


class Attachment(Base):
    """A file added to a conversation. The file itself lives under /data/files; this row describes it."""

    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    environment_id: Mapped[int] = mapped_column(ForeignKey("environments.id"), index=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    # Empty until the attachment is sent with a message.
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id"))
    filename: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(120))
    # image, pdf, text, office, archive, folder or other: decides how the model receives it.
    kind: Mapped[str] = mapped_column(String(20))
    size: Mapped[int] = mapped_column(Integer)
    # Folder relative to /data/files, for example "private/3f2a…".
    folder: Mapped[str] = mapped_column(String(200), unique=True)
    # What preparing the file found: archive overview, file count, extracted text length, warnings.
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)


class ModelPreset(Base):
    """A named model and effort per environment, for example Fast or Deep."""

    __tablename__ = "model_presets"
    __table_args__ = (UniqueConstraint("environment_id", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    environment_id: Mapped[int] = mapped_column(ForeignKey("environments.id"), index=True)
    name: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(80))
    effort: Mapped[str] = mapped_column(String(20))
    position: Mapped[int] = mapped_column(Integer, default=0)
    # Exactly one preset per environment is the default for new conversations.
    is_default: Mapped[bool] = mapped_column(default=False)
    # Models by role; empty falls back (worker: the default preset's, utility: the
    # utility_model setting).
    worker_model: Mapped[str | None] = mapped_column(String(80))
    worker_effort: Mapped[str | None] = mapped_column(String(20))
    utility_model: Mapped[str | None] = mapped_column(String(80))
    utility_effort: Mapped[str | None] = mapped_column(String(20))
    # The longest reply in tokens; None is the model's maximum.
    max_output_tokens: Mapped[int | None] = mapped_column(Integer)
    # Routing (core/routing.py): per reply the utility model names the kind of message and routes maps each kind
    # to a role of this preset; kept while routing is off, so turning it on again restores the assignment.
    routing: Mapped[bool] = mapped_column(Boolean, default=False)
    routes: Mapped[dict[str, str] | None] = mapped_column(JSON)
    # How the lead model hands work to workers: free, or by brief with a check (presets/delegation.py).
    delegation: Mapped[str] = mapped_column(String(10), default="free", server_default="free")
