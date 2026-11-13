"""Agent runs and their steps, evaluations and replays."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.models.base import Base, UtcDateTime, utc_now


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
