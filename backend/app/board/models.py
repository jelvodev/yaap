"""The board's index tables. The card and version files on the board's branch are the truth; these rows are rebuilt
from them (`app.board.index`) and never edited alone, except the links of conversations to cards and the board's own
state. They live beside the board's code, on the shared declarative base; the migrations' environment imports them so
the schema check sees them."""

from datetime import datetime
from typing import Literal

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.models import Base, UtcDateTime, utc_now

StorageKind = Literal["branch", "repository"]
ConversationRole = Literal["chat", "specify", "plan", "build", "review", "other"]


class BoardState(Base):
    """A project's board: where its files are and which commit the index matches."""

    __tablename__ = "board_state"

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    storage: Mapped[StorageKind] = mapped_column(String(12))
    # The board ref's commit the index rows match; None before the first sync.
    head_commit: Mapped[str | None] = mapped_column(String(64))
    last_pushed_commit: Mapped[str | None] = mapped_column(String(64))
    # The next card id. Ids are never reused, also not after a drop or a rebuild from the files.
    next_card_id: Mapped[int] = mapped_column(Integer, default=1)
    synced_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)


class BoardCardRow(Base):
    """One card file as the index knows it; a broken file has a row with `broken` and its error."""

    __tablename__ = "board_cards"
    __table_args__ = (
        UniqueConstraint("project_id", "card_id"),
        Index("ix_board_cards_project_id_board_column", "project_id", "board_column"),
        Index("ix_board_cards_project_id_version", "project_id", "version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    card_id: Mapped[int] = mapped_column(Integer)
    path: Mapped[str] = mapped_column(String(200))
    # Empty for a broken card whose title could not be read.
    title: Mapped[str] = mapped_column(String(200), default="")
    kind: Mapped[str | None] = mapped_column(String(10))
    # `column` is an SQL keyword. None for a broken card.
    board_column: Mapped[str | None] = mapped_column(String(10))
    version: Mapped[str | None] = mapped_column(String(40))
    origin_type: Mapped[str | None] = mapped_column(String(12))
    origin_ref: Mapped[str | None] = mapped_column(String(300))
    waits_for: Mapped[list[int]] = mapped_column(JSON, default=list)
    open_questions: Mapped[int] = mapped_column(Integer, default=0)
    # Whether the card has a specification, for what is needed next.
    has_specification: Mapped[bool] = mapped_column(Boolean, default=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    plan_id: Mapped[int | None] = mapped_column(Integer)
    review_id: Mapped[int | None] = mapped_column(Integer)
    blob_sha: Mapped[str] = mapped_column(String(64))
    broken: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)
    # The card's own `updated` time; None for a broken card.
    updated_at: Mapped[datetime | None] = mapped_column(UtcDateTime())


class BoardVersionRow(Base):
    __tablename__ = "board_versions"
    __table_args__ = (UniqueConstraint("project_id", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(40))
    # open or released; None for a broken version file.
    state: Mapped[str | None] = mapped_column(String(10))
    budget_usd: Mapped[float | None] = mapped_column(Float)
    blob_sha: Mapped[str] = mapped_column(String(64))


class BoardCardConversation(Base):
    """A conversation that belongs to a card, with its role. A conversation belongs to one card at most."""

    __tablename__ = "board_card_conversations"
    __table_args__ = (Index("ix_board_card_conversations_project_id_card_id", "project_id", "card_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    card_id: Mapped[int] = mapped_column(Integer)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), unique=True)
    role: Mapped[ConversationRole] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)

