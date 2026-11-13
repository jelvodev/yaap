"""The shapes of a conversation and its messages, as the API returns them and the reply stream sends them."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agent.policy import AccessLevel
from app.core.conversation_types import MessageRole, MessageStatus
from app.core.message_blocks import ContentBlockOut, blocks_out
from app.core.routing import RouteKind, RouteRole
from app.storage.models import Message


class ImportedFromOut(BaseModel):
    tool: Literal["claude_code", "codex"]
    # The file's name, and upload or the target it was read from.
    file: str
    source: str
    session_id: str | None = None
    cwd: str | None = None
    started_at: datetime | None = None
    imported_at: datetime
    turns: int
    tool_calls: int


class ConversationSummaryOut(BaseModel):
    id: int
    project_id: int | None
    title: str
    provider: str
    model: str
    effort: str
    agent: str | None = None
    pinned_skills: list[str] = Field(default_factory=list)
    updated_at: datetime
    archived_at: datetime | None = None
    # Made by a scheduled task, and a finished task run not seen yet.
    scheduled: bool = False
    unread: bool = False
    access: AccessLevel = "default"
    target_id: int | None = None
    # The conversation's own context limit; null follows the context_limit setting.
    context_limit: int | None = None
    # Set for an imported Claude Code or Codex session.
    imported: ImportedFromOut | None = None


class ReplyRouteOut(BaseModel):
    """Which of the preset's models answered a reply, the kind of message the utility model named, and why."""

    model: str
    model_name: str
    effort: str
    kind: RouteKind | None = None
    role: RouteRole
    reason: str


def route_out(stored: dict[str, Any] | None) -> ReplyRouteOut | None:
    """The stored route as the API shows it; None for a reply without one or with an unreadable one."""
    if not stored:
        return None
    try:
        return ReplyRouteOut.model_validate(stored)
    except ValueError:
        return None


class MessageOut(BaseModel):
    id: int
    parent_id: int | None
    role: MessageRole
    content: list[ContentBlockOut]
    status: MessageStatus
    stop_reason: str | None
    error: str | None
    created_at: datetime
    # Every version of this message at its place in the conversation, oldest first (edits and regenerated replies).
    version_ids: list[int] = Field(default_factory=list)
    # A reply whose preset routes: which model answered and why.
    route: ReplyRouteOut | None = None


def message_to_out(message: Message, version_ids: list[int] | None = None) -> MessageOut:
    return MessageOut(
        version_ids=version_ids if version_ids is not None else [message.id],
        id=message.id,
        parent_id=message.parent_id,
        role=message.role,
        content=blocks_out(message.id, message.content),
        status=message.status,
        stop_reason=message.stop_reason,
        error=message.error,
        created_at=message.created_at,
        route=route_out(message.route),
    )
