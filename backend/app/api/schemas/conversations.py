"""Conversation models: creating and updating conversations, goals, messages and attachments."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.agent.policy import AccessLevel
from app.core.conversation_schemas import ConversationSummaryOut, MessageOut
from app.core.conversation_types import GoalStatus


class ConversationCreate(BaseModel):
    model: str
    effort: str
    # Empty for a conversation outside a project.
    project_id: int | None = None
    # The agent definition to work as, by name; empty for plain YAAP.
    agent: str | None = None
    # Skills to pin from the first message on.
    pinned_skills: list[str] = Field(default_factory=list, max_length=20)
    # The preset the conversation starts with, by name; empty for a custom model and effort.
    preset: str | None = Field(default=None, max_length=40)
    access: AccessLevel = "default"
    # Outside a project: a computer from Settings › Targets to work on; empty for YAAP's container.
    target_id: int | None = None
    # The context limit in tokens; empty follows the context_limit setting.
    context_limit: int | None = None


class PinnedSkillsIn(BaseModel):
    skills: list[str] = Field(max_length=20)


class ConversationMove(BaseModel):
    # The project to move to; null moves the conversation out of its project.
    project_id: int | None


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    # Send agent (null to clear) to change who the conversation works as from the next message on.
    agent: str | None = None
    # True archives the conversation, false restores it.
    archived: bool | None = None
    # Takes effect at once, also for a reply that is running.
    access: AccessLevel | None = None
    # Outside a project and before the first message: the computer to work on (null for YAAP's container).
    target_id: int | None = None
    # The context limit in tokens from the next message on; null follows the context_limit setting.
    context_limit: int | None = None


class ConversationTargetOut(BaseModel):
    """Where a conversation works when it is not YAAP's container."""

    id: int
    name: str
    system: str
    rights: str
    # The project's folder there; empty outside a project (the target's first folder).
    folder: str | None = None
    allow_full_access: bool


class GoalOut(BaseModel):
    id: int
    conversation_id: int
    objective: str
    status: GoalStatus
    max_turns: int
    max_cost: float
    turns: int
    cost: float
    summary: str | None
    created_at: datetime
    finished_at: datetime | None


class GoalUpdate(BaseModel):
    # paused or active (resume), or stopped; done and blocked are for the agent.
    status: Literal["active", "paused", "stopped"] | None = None
    max_turns: int | None = Field(default=None, ge=1, le=200)
    max_cost: float | None = Field(default=None, ge=0, le=1000)


class DefaultSkillsOut(BaseModel):
    # The skills a new conversation starts with: the default_skills setting, limited to skills that exist here.
    skills: list[str]
    # Names in the setting that are not an active skill at this level, so they are left out.
    missing: list[str]


class UtilityEventOut(BaseModel):
    """A utility call made for this conversation, such as setting its title."""

    task: str
    summary: str | None
    model: str
    input_tokens: int
    output_tokens: int
    cost: float | None
    created_at: datetime


class ContextControlOut(BaseModel):
    """How large the conversation may grow, for the context meter."""

    # The limit in force, in tokens: at most the model's window.
    limit: int | None
    # Where it comes from: conversation, project, environment, global, or default (the model's window).
    source: Literal["conversation", "project", "environment", "global", "default"]
    # Whether YAAP compacts by itself, and at which percentage of the limit it summarises and trims.
    auto_compact: bool = True
    compact_at: int = 70
    trim_at: int = 50
    # Claude Code and Codex compact their own sessions; YAAP leaves them alone.
    compacts_itself: bool = False


class ConversationOut(ConversationSummaryOut):
    environment_id: int
    # The computer it works on, from the project's setting or its own choice; null for YAAP's container.
    target: ConversationTargetOut | None = None
    messages: list[MessageOut]
    utility_events: list[UtilityEventOut] = []
    context_tokens: int | None
    context_window: int | None
    context_control: ContextControlOut | None = None
    # The board card this conversation belongs to; null without one or while the project's board is off.
    card_id: int | None = None


class MessageCreate(BaseModel):
    text: str = Field(default="", max_length=200_000)
    # Attachments uploaded to this conversation that go with this message.
    attachment_ids: list[int] = Field(default_factory=list, max_length=20)
    # A user message this one replaces as a new version; the conversation continues from the new version.
    edit_of: int | None = None

    @model_validator(mode="after")
    def has_content(self) -> "MessageCreate":
        if not self.text.strip() and not self.attachment_ids:
            raise ValueError("A message needs text or an attachment.")
        return self


class AttachmentOut(BaseModel):
    id: int
    conversation_id: int
    message_id: int | None
    filename: str
    media_type: str
    kind: str
    size: int
    # Facts found while preparing the file: dimensions, pages, file count, extracted text length.
    details: dict[str, Any]
    created_at: datetime


class BranchSwitch(BaseModel):
    message_id: int
