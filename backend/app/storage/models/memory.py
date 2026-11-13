"""Knowledge documents and the memory and learning proposals built on them."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.models.base import Base, UtcDateTime, utc_now


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
