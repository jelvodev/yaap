"""An imported session as a conversation, and session files on a target to pick from.

The conversation gets one user message per turn and one assistant message with what the agent did: text, reasoning
and tool calls as read-only blocks (marked imported). Nothing is run: the tool calls are shown as they were, with the
results the file recorded. The assistant messages carry an "imported" marker instead of a provider's raw turns, so
every provider gets them as text (imported_model_content), and the conversation can be continued with any model.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath

from sqlalchemy.orm import Session

from app.core.chat import DEFAULT_TITLE, make_title
from app.credentials.crypto import CredentialCipher
from app.imports.sessions import CLAUDE_CODE, CODEX, IMPORTED_BLOCK_TYPE, TOOL_NAMES, ImportedSession, SessionImportError
from app.storage.models import Conversation, Message, Target
from app.targets.base import FileEntry, TargetError, TargetFiles
from app.targets.service import settings_for
from app.targets.ssh import CONNECTIONS, SshTarget

MAX_IMPORT_BYTES = 50 * 1024 * 1024
# Where the two tools keep their sessions, relative to the home folder on a target.
CLAUDE_CODE_FOLDER = ".claude/projects"
CODEX_FOLDER = ".codex/sessions"
CLAUDE_CODE_FILE = re.compile(r"^\.claude/projects/[^/]+/[^/]+\.jsonl$")
CODEX_FILE = re.compile(r"^\.codex/sessions/(?:[^/]+/)*rollout-[^/]+\.jsonl$")
MAX_LISTED = 300
MAX_FOLDERS = 500


def create_conversation(
    session: Session,
    environment_id: int,
    project_id: int | None,
    provider: str,
    model: str,
    effort: str,
    preset: str | None,
    imported: ImportedSession,
    source: str,
    file_name: str,
) -> Conversation:
    """A new conversation with the session's turns; nothing in them is run."""
    now = datetime.now(UTC)
    first_text = next((turn.user_text for turn in imported.turns if turn.user_text), "")
    conversation = Conversation(
        environment_id=environment_id,
        project_id=project_id,
        title=(imported.title or (make_title(first_text) if first_text else DEFAULT_TITLE))[:200],
        provider=provider,
        model=model,
        effort=effort,
        preset=preset,
        pinned_skills=[],
        imported_from={
            "tool": imported.tool,
            "file": file_name,
            "source": source,
            "session_id": imported.session_id,
            "cwd": imported.cwd,
            "started_at": imported.started_at.isoformat() if imported.started_at else None,
            "imported_at": now.isoformat(),
            "turns": len(imported.turns),
            "tool_calls": imported.tool_calls,
        },
        created_at=now,
        updated_at=now,
    )
    session.add(conversation)
    session.flush()
    parent_id: int | None = None
    marker = [{"type": IMPORTED_BLOCK_TYPE, "tool": imported.tool}]
    for turn in imported.turns:
        user = Message(
            conversation_id=conversation.id,
            parent_id=parent_id,
            role="user",
            content=[{"type": "text", "text": turn.user_text or f"(The {TOOL_NAMES[imported.tool]} session went on here.)"}],
            status="complete",
            created_at=turn.at or now,
        )
        session.add(user)
        session.flush()
        parent_id = user.id
        if not turn.blocks:
            continue
        blocks = turn.blocks if any(block["type"] == "text" for block in turn.blocks) else [*turn.blocks, {"type": "text", "text": ""}]
        reply = Message(
            conversation_id=conversation.id,
            parent_id=parent_id,
            role="assistant",
            content=blocks,
            provider_raw=marker,
            status="complete",
            stop_reason="end_turn",
            created_at=turn.reply_at or turn.at or now,
        )
        session.add(reply)
        session.flush()
        parent_id = reply.id
    conversation.head_message_id = parent_id
    session.commit()
    return conversation


# --- Session files on a target -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionFile:
    path: str
    tool: str
    size: int
    modified: float | None
    # Claude Code's folder per working folder (its path with / as -), or Codex's date folder.
    folder: str


def tool_of(path: str) -> str | None:
    if CLAUDE_CODE_FILE.match(path):
        return CLAUDE_CODE
    if CODEX_FILE.match(path):
        return CODEX
    return None


async def list_session_files(files: TargetFiles) -> list[SessionFile]:
    """The Claude Code and Codex session files in the home folder, newest first."""
    found: list[FileEntry] = []
    if (folder := await files.stat(CLAUDE_CODE_FOLDER)) is not None and folder.is_dir:
        projects = [entry for entry in await files.walk(CLAUDE_CODE_FOLDER, False, set(), MAX_FOLDERS) if entry.is_dir]
        for project in projects[:MAX_FOLDERS]:
            found += [entry for entry in await files.walk(project.path, False, set(), 2000) if not entry.is_dir]
    if (folder := await files.stat(CODEX_FOLDER)) is not None and folder.is_dir:
        found += [entry for entry in await files.walk(CODEX_FOLDER, True, set(), 10_000) if not entry.is_dir]
    sessions = []
    for entry in found:
        tool = tool_of(entry.path)
        if tool is None:
            continue
        parent = str(PurePosixPath(entry.path).parent)
        label = parent.removeprefix(CLAUDE_CODE_FOLDER + "/") if tool == CLAUDE_CODE else parent.removeprefix(CODEX_FOLDER + "/")
        sessions.append(SessionFile(entry.path, tool, entry.size, entry.modified, label))
    sessions.sort(key=lambda item: item.modified or 0, reverse=True)
    return sessions[:MAX_LISTED]


async def read_session_file(files: TargetFiles, path: str) -> bytes:
    """A session file from the target; only files in the two session folders, and not larger than the limit."""
    relative = files.check(path)
    if tool_of(relative) is None:
        raise SessionImportError("Only Claude Code and Codex session files can be imported from a target.")
    entry = await files.stat(relative)
    if entry is None or entry.is_dir:
        raise SessionImportError(f"{path} is not there anymore.")
    if entry.size > MAX_IMPORT_BYTES:
        raise SessionImportError("The session file is larger than 50 MB.")
    return await files.read_bytes(relative)


def home_files(session: Session, cipher: CredentialCipher, target: Target) -> TargetFiles:
    """The home folder of the user YAAP signs in as on the target, read-only for the import: only the two session
    folders are listed and read, whatever folders the target allows the agent."""
    if not target.host_key:
        raise TargetError(f"The target {target.name} is not set up yet: test it in Settings › Targets first.")
    key = f"target-{target.id}"
    connection = CONNECTIONS.open.get(key)
    used_by = "" if connection is not None and not connection.is_closed() else f"Session import from {target.name}"
    runtime = SshTarget(key, settings_for(session, cipher, target, used_by))
    # SFTP resolves relative paths against the home folder.
    return runtime.files(".")


__all__ = ["SessionImportError", "create_conversation", "home_files"]
