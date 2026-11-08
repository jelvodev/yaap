"""The shared document in a run: the open_document tool, the agent's turns on the document, and the
user's changes delivered with the next tool result (as steering is).

`with_document` wraps read_file, write_file and edit_file. On the open document it records what the agent saw and
changed, announces the agent's change to the panel (`document_writing`, then `document_change`), and refuses a write
over a change of the user that the agent has not seen, with the diff. `deliver_document_changes` appends the user's
change since the agent last saw the document to a tool result.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import anyio

from app.agent.guard import remember_file
from app.agent.runs import AgentRun
from app.agent.tool_context import files
from app.agent.toolkit import STRING, Tool, ToolError, ToolOutput, schema, text_argument, with_text
from app.checkpoints.service import call_key
from app.documents.service import (
    DocumentError,
    hash_of,
    is_markdown,
    read_version,
    text_of,
    unified_diff,
    user_change_note,
)
from app.targets.base import TargetError

logger = logging.getLogger(__name__)

FILE_TOOLS = frozenset({"read_file", "write_file", "edit_file"})


async def open_document(run: AgentRun, arguments: dict[str, Any]) -> str:
    link = run.documents
    if link is None:
        raise ToolError("Documents open only in a project conversation.")
    target = files(run)
    path = target.check(text_argument(arguments, "path"), for_write=True)
    if not is_markdown(path):
        raise ToolError("Only a Markdown file (.md or .markdown) opens as a document.")
    entry = await target.stat(path)
    if entry is not None and entry.is_dir:
        raise ToolError(f"{path} is a folder.")
    current = await target.read_bytes(path) if entry is not None else None
    try:
        await anyio.to_thread.run_sync(link.open, path, current)
    except DocumentError as error:
        raise ToolError(str(error)) from error
    run.events.put_nowait({"event": "document_open", "path": path})
    if current is None:
        return f"{path} does not exist yet. It is open beside the chat and appears there when you create it with write_file."
    return (
        f"Opened {path} beside the chat, where the user sees it and can edit it. Read it with read_file before you "
        "change it; the user reviews each change you make. The user's saved edits reach you with your next tool result."
    )


async def with_document(
    run: AgentRun,
    name: str,
    arguments: dict[str, Any],
    handler: Callable[[AgentRun, dict[str, Any]], Awaitable[Any]],
) -> Any:
    """Run a file tool; on the conversation's open document, take part in the turns."""
    link = run.documents
    if link is None or name not in FILE_TOOLS:
        return await handler(run, arguments)
    document = await anyio.to_thread.run_sync(link.load)
    if document is None:
        return await handler(run, arguments)
    target = files(run)
    try:
        path = target.check(str(arguments.get("path") or ""))
    except TargetError:
        return await handler(run, arguments)
    if path != document.path:
        return await handler(run, arguments)
    if name == "read_file":
        output = await handler(run, arguments)
        await anyio.to_thread.run_sync(link.seen, path, await read_version(target, path))
        return output
    before = await read_version(target, path)
    before_hash = hash_of(before)
    if before_hash != document.known_hash and run.state.file_hashes.get(path, "") != before_hash:
        # The user changed it since the agent last saw it: tell the agent how, and change nothing.
        diff = unified_diff(path, document.known_text, text_of(before))
        await anyio.to_thread.run_sync(link.seen, path, before)
        told(run, path, before)
        link.unchecked = False
        raise ToolError(
            f"{path} was not changed: the user changed it since you last saw it.\n\n{user_change_note(path, diff)}\n\n"
            "Make your change again on top of theirs."
        )
    run.events.put_nowait({"event": "document_writing", "path": path})
    try:
        output = await handler(run, arguments)
    except BaseException:
        # Nothing was written; the panel stops waiting.
        run.events.put_nowait({"event": "document_change", "path": path, "hash": before_hash})
        raise
    after = await read_version(target, path)
    checkpoint_id = (
        await anyio.to_thread.run_sync(link.checkpoint_for, call_key(name, arguments)) if run.checkpoints else None
    )
    await anyio.to_thread.run_sync(link.agent_changed, path, before, after, checkpoint_id)
    run.events.put_nowait({"event": "document_change", "path": path, "hash": hash_of(after)})
    return output


def told(run: AgentRun, path: str, data: bytes | None) -> None:
    """The agent was told how the document changed, so it knows this version: its own check lets it edit again."""
    if data is not None and path in run.state.file_hashes:
        remember_file(run, path, data)


async def deliver_document_changes(
    run: AgentRun, name: str, arguments: dict[str, Any], output: str | ToolOutput
) -> str | ToolOutput:
    """The user's change of the open document since the agent last saw it goes to the model with this tool result."""
    link = run.documents
    if link is None or not link.unchecked or run.parent_token is not None:
        return output
    link.unchecked = False
    try:
        document = await anyio.to_thread.run_sync(link.load)
        if document is None:
            return output
        current = await read_version(files(run), document.path)
        if hash_of(current) == document.known_hash:
            return output
        diff = unified_diff(document.path, document.known_text, text_of(current))
        await anyio.to_thread.run_sync(link.seen, document.path, current)
        told(run, document.path, current)
    except (ToolError, TargetError, OSError) as error:
        logger.warning("The shared document of conversation %s could not be checked: %s", run.conversation_id, error)
        return output
    run.events.put_nowait({"event": "document_seen", "call_key": call_key(name, arguments), "path": document.path})
    note = "\n\n" + user_change_note(document.path, diff)
    return with_text(output, str(output) + note)


DOCUMENT_TOOLS = [
    Tool(
        "open_document",
        "Open a Markdown file of the project as the shared document in a panel beside the chat, where the user reads "
        "and edits it with you. Use it when you write or revise a document together with the user (a plan, notes, a "
        "README). The user reviews each change you make there, and their saved edits reach you with your next tool "
        "result. The file may not exist yet; create it with write_file.",
        schema({"path": STRING}, ["path"]),
        "auto",
        open_document,
        read_only=True,
    ),
]
