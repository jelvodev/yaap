"""Accounts, auth sessions, A2A tasks, MCP client links and plugin state."""

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.models.base import Base, UtcDateTime, utc_now


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
