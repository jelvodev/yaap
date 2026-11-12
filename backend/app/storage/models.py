"""SQLAlchemy models. Every schema change needs an Alembic migration."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


def utc_now() -> datetime:
    return datetime.now(UTC)


class UtcDateTime(TypeDecorator[datetime]):
    """A point in time, always UTC. SQLite keeps no zone, so values are stored as UTC and come back marked as UTC;
    without the mark, the API sends times the browser reads as local time."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, _dialect: Any) -> datetime | None:
        if value is not None and value.tzinfo is not None:
            return value.astimezone(UTC).replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, _dialect: Any) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class Base(DeclarativeBase):
    pass


class Environment(Base):
    __tablename__ = "environments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    # Folder name under /workspace and /blueprint. Set once at creation; renaming the environment keeps it.
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    colour: Mapped[str] = mapped_column(String(7))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("environment_id", "slug"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    environment_id: Mapped[int] = mapped_column(ForeignKey("environments.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(80))
    # Working folder relative to /workspace, for example "work/website". Set once at creation.
    folder: Mapped[str] = mapped_column(String(200), unique=True)
    repository_url: Mapped[str | None] = mapped_column(String(400))
    # What a new conversation in this project starts with, by name; empty means the environment's default.
    default_agent: Mapped[str | None] = mapped_column(String(80))
    default_preset: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)


class Setting(Base):
    """A setting value at one level: global (no environment, no project), one environment, or one project."""

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    environment_id: Mapped[int | None] = mapped_column(ForeignKey("environments.id"), index=True)
    project_id: Mapped[int | None] = mapped_column(Integer)
    key: Mapped[str] = mapped_column(String(80))
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)


class Credential(Base):
    __tablename__ = "credentials"
    __table_args__ = (UniqueConstraint("environment_id", "kind"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    environment_id: Mapped[int] = mapped_column(ForeignKey("environments.id"))
    kind: Mapped[str] = mapped_column(String(40))
    encrypted_value: Mapped[bytes]
    last_four: Mapped[str] = mapped_column(String(4))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)
    last_tested_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    last_test_result: Mapped[str | None] = mapped_column(Text)


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
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    provider_raw: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="complete")
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


class AgentRunRecord(Base):
    """One agent run: a reply in which the model could use YAAP's tools."""

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    environment_id: Mapped[int] = mapped_column(ForeignKey("environments.id"), index=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"))
    # The reply the run produced; empty while it runs.
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id"))
    agent: Mapped[str | None] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(80))
    # running, complete, incomplete, refused or error
    status: Mapped[str] = mapped_column(String(20), default="running")
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    # Every approval asked during the run: tool, summary, decision and time.
    approvals: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    started_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    # Delegated sub-runs: the run that started it, its task, and its steps and report.
    parent_run_id: Mapped[int | None] = mapped_column(Integer, index=True)
    task: Mapped[str | None] = mapped_column(Text)
    transcript: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    # The worker a sub-run was given: a preset's name, claude_code or codex; empty is the default.
    worker: Mapped[str | None] = mapped_column(String(40))
    # The Blueprint commit of each layer when the run started (app/blueprint/versions.py).
    blueprint_versions: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)


class RunStep(Base):
    """One model call or tool call of an agent run, in order (app/tracing)."""

    __tablename__ = "run_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    # "model", "tool" or "note"
    kind: Mapped[str] = mapped_column(String(10))
    started_at: Mapped[datetime] = mapped_column(UtcDateTime())
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    # Model calls: the model that served it, its tokens and cost, the calls a CLI made, and the context snapshot.
    model: Mapped[str | None] = mapped_column(String(80))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float | None] = mapped_column(Float)
    model_calls: Mapped[int | None] = mapped_column(Integer)
    snapshot: Mapped[str | None] = mapped_column(String(64))
    # Tool calls: the tool, a short summary of its arguments, its stored output, and the approval decision.
    tool: Mapped[str | None] = mapped_column(String(120))
    summary: Mapped[str | None] = mapped_column(String(300))
    output_ref: Mapped[int | None] = mapped_column(Integer)
    is_error: Mapped[bool] = mapped_column(Boolean, default=False)
    approval: Mapped[str | None] = mapped_column(String(20))
    # What the step tells about the work (app/learning): declined, test_failed_twice or known_search, with its note
    # (the user's reason, the test command, the Blueprint item that already held what was searched for).
    signal: Mapped[str | None] = mapped_column(String(20))
    signal_note: Mapped[str | None] = mapped_column(String(500))
    # A note's kind, text, reason and cost; for a tool call that asked or was refused, its reason and call key.
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class EvalRun(Base):
    """One run of a project's eval cases (app/evals): all cases or one, each played `repeats` times, in the project's
    normal setup or a variant of it."""

    __tablename__ = "eval_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    environment_id: Mapped[int] = mapped_column(ForeignKey("environments.id"))
    # queued, running, done, stopped or error
    status: Mapped[str] = mapped_column(String(20))
    # panel, command or schedule
    trigger: Mapped[str] = mapped_column(String(20))
    # Only this case; empty is every valid case.
    case_name: Mapped[str | None] = mapped_column(String(60))
    repeats: Mapped[int] = mapped_column(Integer, default=1)
    # What differs from the project's normal setup (preset, agent, a Blueprint layer at a git ref); empty is none.
    variant: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # For the variant side of a comparison: the run of the normal setup started with it.
    baseline_run_id: Mapped[int | None] = mapped_column(Integer)
    scheduled_run_id: Mapped[int | None] = mapped_column(Integer)
    # The queued /eval this run was started from. No foreign key: the queue row is deleted in the transaction that
    # creates the run, and the unique value is what lets a second consumer of the same item find the run.
    queue_item_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime())


class EvalResult(Base):
    """One play of one case in an eval run: its verdict, each check's result and what it used."""

    __tablename__ = "eval_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("eval_runs.id", ondelete="CASCADE"), index=True)
    case_name: Mapped[str] = mapped_column(String(60))
    repeat: Mapped[int] = mapped_column(Integer, default=1)
    # queued, running, passed, failed, error or stopped
    status: Mapped[str] = mapped_column(String(20))
    # Each check: kind, name, status, detail and the scorer's cost.
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # Why it errored or stopped, or that the budget stopped it.
    detail: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(80))
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    steps: Mapped[int] = mapped_column(Integer, default=0)
    seconds: Mapped[float] = mapped_column(Float, default=0.0)
    # Calls refused because they would have asked for approval in this unattended play.
    asked: Mapped[int] = mapped_column(Integer, default=0)
    scorer_cost: Mapped[float] = mapped_column(Float, default=0.0)
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id", ondelete="SET NULL"))
    # The reply's run record; its steps (run_steps) and those of its sub-runs are the trace of the play.
    agent_run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id", ondelete="SET NULL"))
    # The throwaway workspace relative to /workspace while it is kept (a failed play keeps it for a while).
    workspace: Mapped[str | None] = mapped_column(String(300))
    workspace_expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime())


class Replay(Base):
    """A replay of one model call of a run (app/replays): a new, unattended run in an archived conversation that
    starts with what that call saw, in a throwaway workspace with the files of that moment, in a variant of the
    original's setup."""

    __tablename__ = "replays"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    environment_id: Mapped[int] = mapped_column(ForeignKey("environments.id"))
    # The original: its conversation, run and model step (with the step's position and context snapshot).
    source_conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    source_run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id", ondelete="SET NULL"))
    source_step_id: Mapped[int] = mapped_column(Integer)
    step_position: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[str] = mapped_column(String(64))
    # The checkpoint that holds the files as that step saw them.
    checkpoint_id: Mapped[int | None] = mapped_column(ForeignKey("checkpoints.id", ondelete="SET NULL"))
    # What differs from the original: preset, model, Blueprint layer and ref.
    variant: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # The Blueprint the replay built: per layer its kind, commit, whether it had changes then, source and ref.
    blueprint: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    # queued, running, done, stopped or error
    status: Mapped[str] = mapped_column(String(20))
    detail: Mapped[str | None] = mapped_column(Text)
    # The replay's own archived conversation and the run record of its reply.
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id", ondelete="SET NULL"))
    agent_run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id", ondelete="SET NULL"))
    # Calls refused because they would have asked for approval in this unattended run.
    asked: Mapped[int] = mapped_column(Integer, default=0)
    # The eval case made from it, once made.
    eval_case: Mapped[str | None] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime())


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
    status: Mapped[str] = mapped_column(String(20))
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


class ProcessRecord(Base):
    """A background process an agent started, such as a dev server."""

    __tablename__ = "processes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    environment_id: Mapped[int] = mapped_column(ForeignKey("environments.id"))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), index=True, nullable=True)
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id"), nullable=True)
    command: Mapped[str] = mapped_column(Text)
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # running, exited (ended by itself), stopped (by the user or the agent), ended (YAAP restarted)
    status: Mapped[str] = mapped_column(String(20))
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ended_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # The last output when it ended, so the project page can still show it.
    output_tail: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)
    ended_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)


class ShellSessionRecord(Base):
    """A shell session of run_command, kept for its conversation across replies."""

    __tablename__ = "shell_sessions"
    __table_args__ = (UniqueConstraint("conversation_id", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(40))
    # Where it runs: "container", or the key of a target over SSH (target-<id>).
    place: Mapped[str] = mapped_column(String(40))
    # Names its state: data/shells/<conversation>/<token>/ in the container, yaap-shell-<token> on a target.
    token: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)
    last_used_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)


class SharedDocument(Base):
    """The Markdown document open beside a conversation, which the user and the agent edit in turns.

    `known_*` is the version the agent last saw (read, wrote, or was told about); what differs from it reaches the
    agent as changed by the user. `review_*` is the agent's turn waiting for the user: the version before its first
    change (`review_created`: the file did not exist), the checkpoint before that change, and the version it left.
    """

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), unique=True)
    path: Mapped[str] = mapped_column(String(1000))
    # user or agent
    opened_by: Mapped[str] = mapped_column(String(10))
    known_hash: Mapped[str | None] = mapped_column(String(64))
    known_text: Mapped[str | None] = mapped_column(Text)
    review_hash: Mapped[str | None] = mapped_column(String(64))
    review_base: Mapped[str | None] = mapped_column(Text)
    review_created: Mapped[bool] = mapped_column(Boolean, default=False)
    review_checkpoint_id: Mapped[int | None] = mapped_column(Integer)
    opened_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)


class SearchChunk(Base):
    """A piece of a message, a project memory or a Blueprint file in the search index.

    `source_key` names what the chunk comes from (message:<id>, memory:<project id>,
    blueprint:<environment id or global>:<project id or ->:<path>); `content_hash` is the hash of that source's whole
    text, so a source is embedded again only when it changes or the embedding model does.
    """

    __tablename__ = "search_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # message, memory or blueprint
    kind: Mapped[str] = mapped_column(String(20))
    source_key: Mapped[str] = mapped_column(String(400), index=True)
    chunk_no: Mapped[int] = mapped_column(Integer)
    # Empty for a global Blueprint file.
    environment_id: Mapped[int | None] = mapped_column(ForeignKey("environments.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    # The Blueprint file's path within its layer.
    path: Mapped[str | None] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    # The embedding model, and the vector as float32 bytes; empty when no model was available (full-text only).
    model: Mapped[str | None] = mapped_column(String(80))
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)


class Checkpoint(Base):
    """The project folder as it was before one agent action.

    `kind` is `action` (taken before a tool call that can change files) or `restore` (the state just before a roll
    back, so the roll back can be undone), `edit` (before the user saves a file in the editor) or `discard` (before the
    user discards uncommitted changes in the Changes panel).
    `call_key` identifies the tool call (name and arguments), so the chat can put the checkpoint next to it. The snapshot itself is `commit` in the project's shadow repository.
    """

    __tablename__ = "checkpoints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id", ondelete="SET NULL"), index=True)
    run_record_id: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))
    tool: Mapped[str] = mapped_column(String(60))
    summary: Mapped[str] = mapped_column(String(300))
    call_key: Mapped[str | None] = mapped_column(String(16))
    commit: Mapped[str] = mapped_column(String(64))
    tree: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)


class CheckpointPin(Base):
    """A checkpoint kept whatever its age or number (an eval case that starts at it, a replay while it runs); the
    pruning of checkpoints skips pinned ones. One row per owner, so each owner releases only its own pin."""

    __tablename__ = "checkpoint_pins"
    __table_args__ = (UniqueConstraint("checkpoint_id", "owner"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    checkpoint_id: Mapped[int] = mapped_column(ForeignKey("checkpoints.id", ondelete="CASCADE"), index=True)
    # Who keeps it: "eval-case:<project>:<name>" or "replay:<id>".
    owner: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)


class Secret(Base):
    """A named secret for placeholders, per environment with a project override.

    The agent writes `{{secret:NAME}}` in a command; YAAP inserts the value only when the command runs and masks it
    in every tool output. The value is stored encrypted and never shown again.
    """

    __tablename__ = "secrets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    environment_id: Mapped[int] = mapped_column(ForeignKey("environments.id", ondelete="CASCADE"), index=True)
    # Empty for a secret of the whole environment.
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    encrypted_value: Mapped[bytes]
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)


class VaultEntry(Base):
    """One value in the vault: one of YAAP's own keys, or a secret the agent may use by name.

    The level is global (no environment), an environment, or a project of it. Keys of YAAP itself (kind other than
    generic) are never offered to the agent; an entry with agent_use is a {{secret:NAME}} placeholder. Each
    environment has one default entry per kind of key; a project-level entry of that kind wins for its project.
    The value is stored encrypted and never returned by the API. The old tables credentials and secrets stay in
    place, unused, until they are removed with approval.
    """

    __tablename__ = "vault_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    environment_id: Mapped[int | None] = mapped_column(ForeignKey("environments.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(40))
    agent_use: Mapped[bool] = mapped_column(Boolean, default=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    encrypted_value: Mapped[bytes]
    last_four: Mapped[str] = mapped_column(String(4), default="")
    # Values that may be shown, such as the public half of an SSH key.
    public_fields: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    description: Mapped[str] = mapped_column(Text, default="")
    # builtin: the value is stored here; later onepassword: the value is fetched by reference.
    source: Mapped[str] = mapped_column(String(20), default="builtin")
    reference: Mapped[str | None] = mapped_column(String(300))
    expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    last_used_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    last_tested_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    last_test_result: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)


class VaultUse(Base):
    """That a vault entry was used, by what and when; never the value. Kept 90 days."""

    __tablename__ = "vault_uses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("vault_entries.id", ondelete="CASCADE"), index=True)
    used_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now, index=True)
    used_by: Mapped[str] = mapped_column(String(200))
    conversation_id: Mapped[int | None] = mapped_column(Integer)


class AuditRecord(Base):
    """A model request that left YAAP: when, for what, to which model, and which kinds of data it carried; never the
    content, and secrets only by placeholder name. Kept a year (app/core/audit.py)."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now, index=True)
    environment_id: Mapped[int] = mapped_column(ForeignKey("environments.id"), index=True)
    # Plain ids without a foreign key: the log outlives a removed project or conversation.
    project_id: Mapped[int | None] = mapped_column(Integer)
    conversation_id: Mapped[int | None] = mapped_column(Integer)
    purpose: Mapped[str] = mapped_column(String(20))
    # Utility calls: the task (title, commit_message, …).
    task: Mapped[str | None] = mapped_column(String(40))
    provider: Mapped[str] = mapped_column(String(40))
    connection: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(80))
    data_classes: Mapped[list[str]] = mapped_column(JSON, default=list)
    secret_names: Mapped[list[str]] = mapped_column(JSON, default=list)
    # "sent", or "refused" by a check before the request left, with the reason.
    outcome: Mapped[str] = mapped_column(String(20), default="sent")
    reason: Mapped[str | None] = mapped_column(String(500))


class Target(Base):
    """A machine the agent can work on over SSH, such as the computer that runs YAAP.

    The private key is a vault entry of kind ssh_key; the host key is pinned only after the user confirmed its
    fingerprint. Two switches, both off by default: allow_full_access lets conversations on this target use the
    access choice full, and whole_computer lets file tools go anywhere instead of only the listed folders.
    """

    __tablename__ = "targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    environment_id: Mapped[int] = mapped_column(ForeignKey("environments.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer, default=22)
    user: Mapped[str] = mapped_column(String(80))
    # Detected by the test: linux, macos or windows; and the shell commands run in.
    system: Mapped[str] = mapped_column(String(20), default="unknown")
    shell: Mapped[str] = mapped_column(String(20), default="bash")
    # standard or administrator, as the test found; unknown before the first test.
    rights: Mapped[str] = mapped_column(String(20), default="unknown")
    folders: Mapped[list[str]] = mapped_column(JSON, default=list)
    key_entry_id: Mapped[int | None] = mapped_column(ForeignKey("vault_entries.id", ondelete="SET NULL"))
    host_key: Mapped[str | None] = mapped_column(Text)
    pending_host_key: Mapped[str | None] = mapped_column(Text)
    allow_full_access: Mapped[bool] = mapped_column(Boolean, default=False)
    whole_computer: Mapped[bool] = mapped_column(Boolean, default=False)
    last_tested_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    last_test_result: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)
    # "sandbox" is a project's own container, made and pinned by YAAP (app/sandbox), not listed in
    # Settings › Targets; network is its switch, last_used_at feeds the idle stop.
    kind: Mapped[str] = mapped_column(String(20), default="ssh")
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), unique=True)
    network: Mapped[bool] = mapped_column(Boolean, default=False)
    last_used_at: Mapped[datetime | None] = mapped_column(UtcDateTime())


class BackupRun(Base):
    """One backup: made by hand, on schedule or once at start for a missed one, and copied to the
    destinations that are on. results has one item per destination: {"destination", "ok", "message"}."""

    __tablename__ = "backup_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trigger: Mapped[str] = mapped_column(String(20))
    # running, ok, partial (made, but a destination failed) or failed.
    status: Mapped[str] = mapped_column(String(20), default="running")
    started_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    name: Mapped[str | None] = mapped_column(String(60))
    size: Mapped[int | None] = mapped_column(Integer)
    results: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text)


class KnowledgeDocument(Base):
    """A reference document of a project: stored in YAAP's data folder, not in the
    repository, as the original and its extracted text, and indexed for search."""

    __tablename__ = "knowledge_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    # pdf, office, text or archive, as attachments are recognised
    kind: Mapped[str] = mapped_column(String(20))
    size: Mapped[int] = mapped_column(Integer)
    # Characters of extracted text.
    chars: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)
    # A web page's address (refreshable), or the file in the project's knowledge folder it mirrors.
    source_url: Mapped[str | None] = mapped_column(Text)
    source_path: Mapped[str | None] = mapped_column(Text)
    # SHA-256 of the source file, so a folder file is read again only when it changed.
    content_hash: Mapped[str | None] = mapped_column(String(64))
    fetched_at: Mapped[datetime | None] = mapped_column(UtcDateTime())


class MemorySuggestion(Base):
    """An entry the utility model proposes for the project memory; the user accepts, edits or dismisses it.
   """

    __tablename__ = "memory_suggestions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    # The conversation it came from; kept as a plain number, so deleting the conversation keeps the suggestion.
    conversation_id: Mapped[int | None] = mapped_column(Integer)
    section: Mapped[str] = mapped_column(String(40))
    text: Mapped[str] = mapped_column(Text)
    # The existing entry this would replace, when the suggestion says nearly the same.
    updates_entry: Mapped[str | None] = mapped_column(Text)
    # open, accepted or dismissed
    status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)


class LearningProposal(Base):
    """A change the work suggests (app/learning): a rule, a skill step or a rule's removal in a Blueprint layer, or a
    line in the project memory. Nothing is applied until the user accepts it."""

    __tablename__ = "learning_proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    # The conversation it came from; a plain number, so deleting the conversation keeps the proposal.
    conversation_id: Mapped[int | None] = mapped_column(Integer)
    # rule, memory, skill_step or rule_removal
    kind: Mapped[str] = mapped_column(String(20))
    # global, environment or project: the Blueprint layer it changes (project for a memory line)
    level: Mapped[str] = mapped_column(String(20))
    # The file in the level's layer, or the memory section of a memory line.
    target: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)
    diff: Mapped[str] = mapped_column(Text, default="")
    rationale: Mapped[str] = mapped_column(Text, default="")
    # The steps it rests on: id, signal, tool, summary and note of each.
    signals: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # utility, fallback or check
    source: Mapped[str] = mapped_column(String(20))
    # open, accepted or declined
    status: Mapped[str] = mapped_column(String(20))
    # The branch of the layer's repository that holds the change while it waits.
    branch: Mapped[str | None] = mapped_column(String(120))
    # ready, measuring, not_measured or not_needed; measuring reads as measured once both eval runs are finished.
    measurement: Mapped[str] = mapped_column(String(20))
    measurement_note: Mapped[str | None] = mapped_column(Text)
    # The comparison's runs without and with the change; plain numbers, so removing a run keeps the proposal.
    baseline_run_id: Mapped[int | None] = mapped_column(Integer)
    variant_run_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)
    decided_at: Mapped[datetime | None] = mapped_column(UtcDateTime())


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



class MemoryMerge(Base):
    """A merge of duplicate or overlapping project memory entries that the utility model proposes; the user accepts
    or rejects it. Nothing changes in the memory until it is accepted."""

    __tablename__ = "memory_merges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    # The entries it replaces, in the order of the memory file: [{"section": ..., "text": ...}].
    entries: Mapped[list[dict[str, str]]] = mapped_column(JSON)
    # The merged entry, which takes the place of the first entry it replaces.
    section: Mapped[str] = mapped_column(String(80))
    text: Mapped[str] = mapped_column(Text)
    # open, accepted, rejected, or outdated (an entry it replaces is gone or changed)
    status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)
    decided_at: Mapped[datetime | None] = mapped_column(UtcDateTime())


class MemoryTidyState(Base):
    """When a project's memory was last tidied up, and how many entries it had then."""

    __tablename__ = "memory_tidy_state"

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    entries_at_check: Mapped[int] = mapped_column(Integer, default=0)
    checked_at: Mapped[datetime | None] = mapped_column(UtcDateTime())


class Account(Base):
    """The one local account of this YAAP: its password as an Argon2id hash. One row, id 1; no row
    means no password was set yet, and the browser shows the first-run screen."""

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)


class AuthSession(Base):
    """A signed-in browser. Only hashes are stored: of the session cookie's value (the id) and of
    the CSRF token that state-changing requests send in a header."""

    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime())
    user_agent: Mapped[str] = mapped_column(String(200), default="")


class A2ATask(Base):
    """A task another agent gave YAAP over A2A: one reply in a conversation of the token's scope.

    The task's state is read from that reply; a context (context_id) is one conversation."""

    __tablename__ = "a2a_tasks"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    context_id: Mapped[str] = mapped_column(String(100), index=True)
    # The API token it came with, and that token's scope.
    token_id: Mapped[int] = mapped_column(Integer)
    environment_id: Mapped[int] = mapped_column(Integer)
    project_id: Mapped[int | None] = mapped_column(Integer)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    user_message_id: Mapped[int] = mapped_column(Integer)
    canceled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)


class McpClientConversation(Base):
    """The conversation that logs the tool calls of an outside MCP client, one per API token."""

    __tablename__ = "mcp_client_conversations"

    token_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)


class PluginState(Base):
    """A plugin in data/plugins/<name> that was switched on or off in Settings. A plugin without a
    row is off, so a folder that appears in data/plugins never runs by itself."""

    __tablename__ = "plugins"

    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Why YAAP switched it off: the error at import or registration, shown in Settings until it is switched on again.
    error: Mapped[str | None] = mapped_column(Text)
    failed_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    # Set while it loads; still set on the next start means YAAP stopped while loading it.
    loading_since: Mapped[datetime | None] = mapped_column(UtcDateTime())
    changed_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)
