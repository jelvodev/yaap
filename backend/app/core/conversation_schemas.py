"""The shapes of a conversation and its messages, as the API returns them and the reply stream sends them."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agent.policy import AccessLevel
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


class ContentBlockOut(BaseModel):
    # command: what a /command expanded to (text); skill: a pinned skill's full text; skill_removed: unpinned.
    # summary: on the last message a summary covers; the model gets it instead of what it covers.
    # note: a line YAAP added to the conversation, such as a move to another project.
    # trim: the model gets the long tool results up to this message shortened.
    # repo_map: the store key of the repository map kept for the conversation (not shown).
    # plan: a plan the agent handed over with submit_plan, by its id (the card reads the plan itself).
    type: Literal[
        "text",
        "reasoning",
        "tool_call",
        "attachment",
        "command",
        "skill",
        "skill_removed",
        "goal",
        "summary",
        "trim",
        "steering",
        "note",
        "repo_map",
        "plan",
    ]
    # Text and reasoning blocks.
    text: str = ""
    # Tool call blocks: what the agent did and what came back.
    id: str | None = None
    name: str | None = None
    input: dict[str, Any] | None = None
    output: str | None = None
    is_error: bool = False
    # Attachment blocks (the name is in name).
    attachment_id: int | None = None
    kind: str | None = None
    media_type: str | None = None
    size: int | None = None
    # Goal blocks: which turn of the goal this message starts.
    turn: int | None = None
    max_turns: int | None = None
    # Summary blocks: how many messages of the branch the summary covers, and the focus asked with /compact.
    covered: int | None = None
    focus: str | None = None
    # Tool calls of an imported session: shown as they were, never run by YAAP.
    imported: bool | None = None
    # Plan blocks: the plan's id.
    plan_id: int | None = None


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
    role: Literal["user", "assistant"]
    content: list[ContentBlockOut]
    status: Literal["complete", "incomplete", "refused", "error"]
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
        content=[ContentBlockOut(**block) for block in message.content],
        status=message.status,
        stop_reason=message.stop_reason,
        error=message.error,
        created_at=message.created_at,
        route=route_out(message.route),
    )
