"""An imported session as a conversation, and session files on a target to pick from.

The conversation gets one user message per turn and one assistant message with what the agent did: text, reasoning
and tool calls as read-only blocks (marked imported). Nothing is run: the tool calls are shown as they were, with the
results the file recorded. The assistant messages carry an "imported" marker instead of a provider's raw turns, so
every provider gets them as text (imported_model_content), and the conversation can be continued with any model.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath

from sqlalchemy.orm import Session

from app.core.chat_history import DEFAULT_TITLE, make_title
from app.core.hidden_text import describe, find_hidden
from app.credentials.crypto import CredentialCipher
from app.imports.sessions import IMPORTED_BLOCK_TYPE
from app.providers.registry import SESSION_FORMATS, session_format
from app.providers.session_format import ImportedSession, SessionFormat, SessionImportError, SessionTool
from app.storage.models import Conversation, Message, Target
from app.targets.base import FileEntry, TargetError, TargetFiles
from app.targets.service import settings_for
from app.targets.ssh import CONNECTIONS, SshTarget

MAX_IMPORT_BYTES = 50 * 1024 * 1024
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
    known = session_format(imported.tool)
    tool_label = known.label if known is not None else imported.tool
    marker = [{"type": IMPORTED_BLOCK_TYPE, "tool": imported.tool}]
    for turn in imported.turns:
        user = Message(
            conversation_id=conversation.id,
            parent_id=parent_id,
            role="user",
            content=[{"type": "text", "text": turn.user_text or f"(The {tool_label} session went on here.)"}],
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


def strings_of(value: object) -> list[str]:
    """Every string inside a block of a session (text, reasoning, tool input and output)."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in strings_of(item)]
    if isinstance(value, list):
        return [text for item in value for text in strings_of(item)]
    return []


def hidden_warnings(imported: ImportedSession) -> list[str]:
    """What the session hides from a reader: tag characters, direction controls, invisible or lookalike characters.
    The session is imported as it is; the warning tells the user to look at it before continuing it."""
    texts = [text for turn in imported.turns for text in [turn.user_text, *strings_of(turn.blocks)]]
    warning = describe(find_hidden("\n".join(texts)))
    return [warning] if warning else []


# --- Session files on a target -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionFile:
    path: str
    tool: SessionTool
    size: int
    modified: float | None
    # The tool's folder the file is in: Claude Code's per working folder (its path with / as -), Codex's per date.
    folder: str


def format_of(path: str) -> SessionFormat | None:
    return next((item for item in SESSION_FORMATS if item.file_pattern.match(path)), None)


def tool_of(path: str) -> SessionTool | None:
    found = format_of(path)
    return found.tool if found is not None else None


async def list_session_files(files: TargetFiles) -> list[SessionFile]:
    """The session files of every known tool in the home folder, newest first."""
    sessions = []
    for session_type in SESSION_FORMATS:
        found: list[FileEntry] = []
        if (folder := await files.stat(session_type.folder)) is not None and folder.is_dir:
            if session_type.per_folder:
                projects = [
                    entry for entry in await files.walk(session_type.folder, False, set(), MAX_FOLDERS) if entry.is_dir
                ]
                for project in projects[:MAX_FOLDERS]:
                    found += [entry for entry in await files.walk(project.path, False, set(), 2000) if not entry.is_dir]
            else:
                found += [
                    entry for entry in await files.walk(session_type.folder, True, set(), 10_000) if not entry.is_dir
                ]
        for entry in found:
            owner = format_of(entry.path)
            if owner is None:
                continue
            parent = str(PurePosixPath(entry.path).parent)
            label = parent.removeprefix(session_type.folder + "/")
            sessions.append(SessionFile(entry.path, owner.tool, entry.size, entry.modified, label))
    sessions.sort(key=lambda item: item.modified or 0, reverse=True)
    return sessions[:MAX_LISTED]


async def read_session_file(files: TargetFiles, path: str) -> bytes:
    """A session file from the target; only files in the two session folders, and not larger than the limit."""
    relative = files.check(path)
    if tool_of(relative) is None:
        names = " and ".join(item.label for item in SESSION_FORMATS)
        raise SessionImportError(f"Only {names} session files can be imported from a target.")
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


__all__ = ["SessionImportError", "create_conversation", "hidden_warnings", "home_files"]
