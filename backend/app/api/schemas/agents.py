"""Agent models: agent definitions, their tools, runs and the workers a task can be delegated to."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


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


class ToolPermissionOut(BaseModel):
    name: str
    label: str
    default: str
