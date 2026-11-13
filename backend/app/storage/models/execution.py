"""Processes, shell sessions, shared documents, search chunks and checkpoints."""

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.models.base import Base, UtcDateTime, utc_now


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
