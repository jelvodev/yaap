"""Request and response models of the HTTP API."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.agent.policy import AccessLevel
from app.core.conversation_types import GoalStatus
from app.core.routing import RouteKind, RouteRole
# The conversation and message shapes live in core, where the reply stream uses them too.
from app.core.conversation_schemas import (
    ContentBlockOut as ContentBlockOut,
    ConversationSummaryOut as ConversationSummaryOut,
    ImportedFromOut as ImportedFromOut,
    MessageOut as MessageOut,
    message_to_out as message_to_out,
)
from app.presets.delegation import DelegationMode

HEX_COLOUR = r"^#[0-9A-Fa-f]{6}$"


class EnvironmentOut(BaseModel):
    id: int
    name: str
    colour: str


SettingSource = Literal["default", "global", "environment", "project"]


class SettingOption(BaseModel):
    value: str
    label: str


class SettingOut(BaseModel):
    key: str
    label: str
    description: str
    # Fixed choices for this setting; empty when the value is free or comes from elsewhere (such as the models).
    options: list[SettingOption]
    value: Any
    source: SettingSource
    inherited_value: Any
    inherited_source: SettingSource
    # What is stored at this level itself; null when the level inherits.
    level_value: Any = None


class SettingValue(BaseModel):
    value: Any


class BudgetOut(BaseModel):
    scope: Literal["environment", "total"]
    label: str
    limit: float
    spent: float


class UsageSummaryOut(BaseModel):
    period_start: datetime
    cost: float
    priced_records: int
    unpriced_records: int
    # Replies covered by a subscription (Claude Code): no per-reply cost.
    subscription_records: int
    input_tokens: int
    output_tokens: int
    budgets: list[BudgetOut] = Field(default_factory=list)


class EnvironmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    colour: str = Field(pattern=HEX_COLOUR)


class EnvironmentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    colour: str | None = Field(default=None, pattern=HEX_COLOUR)


class CredentialStatusOut(BaseModel):
    kind: str
    label: str
    description: str
    configured: bool
    last_four: str | None
    updated_at: datetime | None
    last_tested_at: datetime | None
    last_test_result: str | None


class CredentialSave(BaseModel):
    value: str = Field(min_length=8, max_length=500)


class CredentialTestOut(BaseModel):
    ok: bool
    message: str


class ModelPricingOut(BaseModel):
    input_per_million: float
    output_per_million: float
    cache_read_per_million: float
    cache_write_per_million: float


class ModelOut(BaseModel):
    provider: str
    id: str
    display_name: str
    context_window: int
    # The longest reply the model can write, in tokens.
    max_output_tokens: int
    efforts: list[str]
    default_effort: str
    pricing: ModelPricingOut
    # The vendor, for grouping in model lists, for example "OpenAI".
    provider_label: str = ""
    # Per environment only: whether the environment can use the model now, and if not, what is missing.
    available: bool = True
    unavailable_reason: str | None = None
    # Per environment only: False when the model policy forbids the model; unavailable_reason then names the policy
    # and its level.
    allowed: bool = True
    # The catalog's mark for business data processing, which a model policy can require.
    business_data: bool = False


class ProviderOut(BaseModel):
    name: str
    connection: str
    display_name: str
    credential_kind: str | None
    # The models run on the user's own computer (Ollama): what a model policy's "only local models" allows.
    local: bool = False


class ClaudeCodeStatusOut(BaseModel):
    installed: bool
    version: str | None
    logged_in: bool
    auth_method: str | None
    subscription_type: str | None
    email: str | None
    sign_in_command: str


class CodexStatusOut(BaseModel):
    installed: bool
    version: str | None
    logged_in: bool
    # Codex's own line about the sign-in, such as "Logged in using ChatGPT".
    detail: str | None
    sign_in_command: str


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    # Clone this repository into the project folder; empty for a new, empty repository.
    repository_url: str | None = Field(default=None, max_length=400)
    # From a template: create the GitHub repository repository_name from this template repository
    # (owner/name), then clone it.
    template: str | None = Field(default=None, max_length=200, pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    repository_name: str | None = Field(default=None, max_length=100, pattern=r"^[A-Za-z0-9._-]+$")
    private: bool = True


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    # Send these (null to clear) to change what new conversations in the project start with.
    default_agent: str | None = None
    default_preset: str | None = None


class ProjectMove(BaseModel):
    environment_id: int


class ProjectMoveOut(BaseModel):
    project: ProjectOut
    # Defaults that do not exist in the new environment and were cleared, for example "preset Deep".
    cleared: list[str]


class ProjectMemoryOut(BaseModel):
    content: str
    # The empty template when the project has no memory file yet.
    exists: bool


class ProjectMemoryIn(BaseModel):
    content: str = Field(max_length=65_536)


class MemoryEntryOut(BaseModel):
    # Its line and a hash of its text; an edit of a memory that changed meanwhile is refused with 409.
    id: str
    section: str
    text: str


class MemoryEntriesOut(BaseModel):
    # The standard sections first, then any other section the file has.
    sections: list[str]
    entries: list[MemoryEntryOut]
    # After adding an entry: an existing entry that says nearly the same, as a warning.
    similar: str | None = None


class MemoryEntryIn(BaseModel):
    section: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=1000)


class MemoryEntryEdit(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


class ProjectOut(BaseModel):
    id: int
    environment_id: int
    name: str
    # Working folder relative to the workspace, for example "work/website".
    folder: str
    repository_url: str | None
    default_agent: str | None = None
    default_preset: str | None = None
    created_at: datetime
    # Whether the project opens its board (the project_board setting); off, a click only narrows the conversations.
    board_enabled: bool = True



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


class PresetRouteOut(BaseModel):
    """Which of the preset's roles answers one kind of message when the preset routes."""

    kind: RouteKind
    label: str
    role: RouteRole


class PresetOut(BaseModel):
    id: int
    environment_id: int
    name: str
    model: str
    effort: str
    is_default: bool
    # Models by role; empty means the fallback.
    worker_model: str | None = None
    worker_effort: str | None = None
    utility_model: str | None = None
    utility_effort: str | None = None
    # The longest reply in tokens; null is the model's maximum.
    max_output_tokens: int | None = None
    # Conversations outside a project start with this preset (the plain_chat_preset setting).
    plain_chats: bool = False
    # Routing: per reply the utility model names the kind of message, and each kind goes to a role.
    routing: bool = False
    routes: list[PresetRouteOut] = Field(default_factory=list)
    delegation: DelegationMode = "free"


class VendorOut(BaseModel):
    """A vendor the presets can switch to, with the models a switch picks, largest first."""

    name: str
    label: str
    models: list[str]


class PresetVendorOut(BaseModel):
    vendors: list[VendorOut]
    # The vendor all presets use; null when they mix vendors.
    current: str | None = None


class PresetVendorSwitch(BaseModel):
    vendor: str = Field(min_length=1, max_length=40)


class PresetVendorSwitched(PresetVendorOut):
    # How many models moved, in the presets and the environment's utility model.
    changed: int


class PresetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    model: str
    effort: str
    worker_model: str | None = None
    worker_effort: str | None = None
    utility_model: str | None = None
    utility_effort: str | None = None
    # The longest reply in tokens; null is the model's maximum.
    max_output_tokens: int | None = None
    # Left out, a new preset delegates freely.
    delegation: DelegationMode | None = None


class PresetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    model: str | None = None
    effort: str | None = None
    # Only true is meaningful: making another preset the default unsets this one.
    is_default: bool | None = None
    # Send a role's model as null or "" to clear it.
    worker_model: str | None = None
    worker_effort: str | None = None
    utility_model: str | None = None
    utility_effort: str | None = None
    # Send null to return to the model's maximum.
    max_output_tokens: int | None = None
    # Turn routing on or off; the kinds sent change their role, the others keep theirs.
    routing: bool | None = None
    routes: dict[RouteKind, RouteRole] | None = None
    delegation: DelegationMode | None = None


class AgentOut(BaseModel):
    name: str
    description: str
    preset: str | None
    # A model role instead of a preset, such as worker.
    role: str | None = None
    # None: every tool the conversation offers.
    tools: list[str] | None
    skills: list[str]
    commands: list[str] | None
    source_label: str
    path: str
    warnings: list[str]


class AgentToolOut(BaseModel):
    name: str
    description: str
    # Only reads; the rest can change files, run commands or reach outside.
    read_only: bool
    # auto, ask or conditional, before the user's own permission settings.
    permission: str


class AgentTemplateOut(BaseModel):
    name: str
    description: str


class AgentFromTemplate(BaseModel):
    template: str


class RunApprovalOut(BaseModel):
    tool: str
    summary: str
    decision: str
    decided_at: str


class RunOut(BaseModel):
    id: int
    conversation_id: int
    conversation_title: str
    project_id: int | None
    project_name: str | None
    agent: str | None
    model: str
    status: str
    tool_calls: int
    approvals: list[RunApprovalOut]
    started_at: datetime
    finished_at: datetime | None
    # Delegated sub-runs: the run that started it, its task, and its steps (tool calls and report).
    parent_run_id: int | None = None
    task: str | None = None
    transcript: list[dict[str, Any]] = Field(default_factory=list)
    # The worker it was given: a preset's name, claude_code or codex; empty is the default worker.
    worker: str | None = None


class WorkerOut(BaseModel):
    """Who `delegate` can hand a task to: a preset's worker model or a coding agent CLI."""

    # What delegate's worker argument takes: the preset's name, claude_code or codex.
    name: str
    kind: Literal["preset", "cli"]
    label: str
    model: str
    model_name: str
    effort: str
    # How it is reached: the connection's name, such as Anthropic, Claude Code or Codex.
    connection: str
    billing: Literal["metered", "subscription"]
    available: bool
    unavailable_reason: str | None = None
    # The default preset's worker is what delegate uses without a worker in a conversation of that preset.
    is_default: bool = False
    # False when the model policy forbids this worker; unavailable_reason names the policy and its level.
    allowed: bool = True


class SearchHitOut(BaseModel):
    """A message, a project's memory or a Blueprint file. Message fields are set for messages only; path and level
    for Blueprint files."""

    kind: Literal["message", "memory", "knowledge", "blueprint"] = "message"
    conversation_id: int | None = None
    conversation_title: str | None = None
    project_id: int | None = None
    message_id: int | None = None
    role: str | None = None
    path: str | None = None
    level: Literal["global", "environment", "project"] | None = None
    # Matched words are between  and .
    snippet: str
    created_at: datetime | None = None


class UsageTotalsOut(BaseModel):
    calls: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    # Charged per call on metered connections.
    cost: float
    # What subscription calls would have cost at API prices.
    subscription_value: float
    # Metered calls without a known price.
    unpriced_calls: int


class UsageGroupOut(BaseModel):
    key: str
    label: str
    totals: UsageTotalsOut


class UsageReportOut(BaseModel):
    month: str
    months: list[UsageGroupOut]
    totals: UsageTotalsOut
    by_model: list[UsageGroupOut]
    by_provider: list[UsageGroupOut]
    by_purpose: list[UsageGroupOut]
    by_billing: list[UsageGroupOut]
    top_conversations: list[UsageGroupOut]
    by_project: list[UsageGroupOut]
    by_environment: list[UsageGroupOut]


class BackupOut(BaseModel):
    name: str
    size: int
    created_at: datetime


class ToolPermissionOut(BaseModel):
    name: str
    label: str
    default: str
