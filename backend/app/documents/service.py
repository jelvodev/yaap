"""The shared document beside a conversation: one Markdown file of the project that the
user and the agent edit in turns, not at the same time.

- Opening. The user opens it from the Files tab, the agent with `open_document`. The version at that moment counts as
  the one the agent knows.
- The agent's turn. Its `write_file` and `edit_file` of the document show in the panel as they land; the panel stays
  read-only until the user accepts the change or rolls it back to the version before (the checkpoint before the
  agent's first change holds that version too).
- The user's turn. A save in the editor uses the Files tab's hash check. What changed since the agent last saw
  the document reaches the agent as "changed by the user", with the diff, in its next tool result, and the agent
  cannot write the document over a change it has not seen.
"""

import difflib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agent.guard import content_hash
from app.storage.models import Checkpoint, SharedDocument
from app.targets.base import TargetFiles

logger = logging.getLogger(__name__)

MARKDOWN_SUFFIXES = (".md", ".markdown")
# The diff the agent gets with a tool result; longer diffs are cut and the agent reads the file.
MAX_NOTE_DIFF_CHARS = 12_000
USER_CHANGE_NOTE = "[The user changed {path}, the shared document beside the chat, since you last saw it]"


class DocumentError(Exception):
    """The document cannot be opened or changed as asked; the message is safe to show."""


def is_markdown(path: str) -> bool:
    return PurePosixPath(path).suffix.lower() in MARKDOWN_SUFFIXES


def text_of(data: bytes | None) -> str | None:
    return None if data is None else data.decode("utf-8", errors="replace")


def hash_of(data: bytes | None) -> str | None:
    return None if data is None else content_hash(data)


def unified_diff(path: str, old: str | None, new: str | None) -> str:
    """A unified diff from old to new (None: the file does not exist), with \\n line endings."""

    def lines(text: str | None) -> list[str]:
        if not text:
            return []
        split = text.replace("\r\n", "\n").replace("\r", "\n").splitlines(keepends=True)
        if split and not split[-1].endswith("\n"):
            split[-1] += "\n\\ No newline at end of file\n"
        return split

    before = f"a/{path}" if old is not None else "/dev/null"
    after = f"b/{path}" if new is not None else "/dev/null"
    return "".join(difflib.unified_diff(lines(old), lines(new), before, after))


def user_change_note(path: str, diff: str) -> str:
    shown = diff if len(diff) <= MAX_NOTE_DIFF_CHARS else diff[:MAX_NOTE_DIFF_CHARS] + "\n[Diff cut; read the file for the rest.]\n"
    return f"{USER_CHANGE_NOTE.format(path=path)}\n```diff\n{shown.rstrip()}\n```"


async def read_version(files: TargetFiles, path: str) -> bytes | None:
    """The file's bytes, or None when it does not exist (or is a folder)."""
    entry = await files.stat(path)
    if entry is None or entry.is_dir:
        return None
    return await files.read_bytes(path)


def find_document(session: Session, conversation_id: int) -> SharedDocument | None:
    return session.scalar(select(SharedDocument).where(SharedDocument.conversation_id == conversation_id))


def open_document(session: Session, conversation_id: int, path: str, by: str, current: bytes | None) -> SharedDocument:
    """Open path as the conversation's document, in place of another one. Opening the one that is open keeps it as
    it is, with a change that waits for review."""
    if not is_markdown(path):
        raise DocumentError("Only a Markdown file (.md or .markdown) opens as a document.")
    found = find_document(session, conversation_id)
    if found is not None and found.path == path:
        return found
    if found is not None:
        session.delete(found)
        session.flush()
    document = SharedDocument(
        conversation_id=conversation_id,
        path=path,
        opened_by=by,
        known_hash=hash_of(current),
        known_text=text_of(current),
        review_created=False,
        opened_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(document)
    session.commit()
    return document


def close_document(session: Session, conversation_id: int) -> bool:
    found = find_document(session, conversation_id)
    if found is None:
        return False
    session.delete(found)
    session.commit()
    return True


def mark_seen(document: SharedDocument, data: bytes | None) -> None:
    """The agent has seen this version: it read or wrote it, or was told how it changed."""
    document.known_hash = hash_of(data)
    document.known_text = text_of(data)
    document.updated_at = datetime.now(UTC)


def record_agent_change(
    document: SharedDocument, before: bytes | None, after: bytes | None, checkpoint_id: int | None
) -> None:
    """The agent changed the document: its turn starts (or goes on) and waits for the user's review."""
    if document.review_hash is None:
        document.review_base = text_of(before)
        document.review_created = before is None
        document.review_checkpoint_id = checkpoint_id
    document.review_hash = hash_of(after)
    # Changed back to where its turn started: nothing left to review.
    if not document.review_created and text_of(after) == document.review_base:
        clear_review(document)
    mark_seen(document, after)


def clear_review(document: SharedDocument) -> None:
    document.review_hash = None
    document.review_base = None
    document.review_created = False
    document.review_checkpoint_id = None
    document.updated_at = datetime.now(UTC)


def document_part(session: Session, conversation_id: int) -> str | None:
    """What the agent is told about the open document, in its system prompt."""
    document = find_document(session, conversation_id)
    if document is None:
        return None
    return (
        f"Shared document: {document.path} is open in a panel beside the chat, where the user reads and edits it. "
        "Change it with edit_file or write_file; the user sees each change and accepts it or rolls it back. The "
        "user's saved edits reach you with your next tool result, marked as changed by the user; build on them and "
        "never write over them."
    )


@dataclass
class DocumentLink:
    """What a run needs for its conversation's shared document (on AgentRun.documents; a sub-run shares its
    parent's)."""

    session_factory: sessionmaker[Session]
    conversation_id: int
    # Whether the document may have changed outside the agent's file tools since the run last looked: at the start
    # of a run, and after the user saved or rolled it back while the run works.
    unchecked: bool = True

    def load(self) -> SharedDocument | None:
        """The open document as it is stored now, detached from its session."""
        with self.session_factory() as session:
            document = find_document(session, self.conversation_id)
            if document is not None:
                session.expunge(document)
            return document

    def open(self, path: str, current: bytes | None) -> None:
        with self.session_factory() as session:
            open_document(session, self.conversation_id, path, "agent", current)

    def seen(self, path: str, data: bytes | None) -> None:
        with self.session_factory() as session:
            document = find_document(session, self.conversation_id)
            if document is not None and document.path == path:
                mark_seen(document, data)
                session.commit()

    def agent_changed(self, path: str, before: bytes | None, after: bytes | None, checkpoint_id: int | None) -> None:
        with self.session_factory() as session:
            document = find_document(session, self.conversation_id)
            if document is not None and document.path == path:
                record_agent_change(document, before, after, checkpoint_id)
                session.commit()

    def checkpoint_for(self, key: str) -> int | None:
        """The checkpoint this conversation took just before the tool call with this key."""
        with self.session_factory() as session:
            return session.scalar(
                select(Checkpoint.id)
                .where(Checkpoint.conversation_id == self.conversation_id, Checkpoint.call_key == key)
                .order_by(Checkpoint.id.desc())
                .limit(1)
            )


def notify_changed(runs: Any, *, project_id: int | None = None, conversation_id: int | None = None) -> None:
    """The user changed a file of a project (or the document of a conversation): the running runs there look at their
    document again after their next tool call."""
    for run in list(runs.runs.values()):
        link = getattr(run, "documents", None)
        if link is None:
            continue
        if (project_id is not None and run.project_id == project_id) or (
            conversation_id is not None and run.conversation_id == conversation_id
        ):
            link.unchecked = True
