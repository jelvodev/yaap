"""Secrets, the vault, the audit trail, targets and backups."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.models.base import Base, UtcDateTime, utc_now


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
