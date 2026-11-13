"""The file tools: list, read, search, write and edit files in the run's working folder.

Paths are confined to the project folder, or to the folder on a computer over SSH.
"""

import re
from typing import Any

from app.agent.guard import note_outside, refuse_if_changed, remember_file
from app.agent.repeated_reads import repeat_note, text_digest
from app.agent.runs import AgentRun
from app.agent.tool_context import files
from app.agent.toolkit import MAX_OUTPUT_CHARS, STRING, Tool, ToolError, schema, text_argument, truncate
from app.blueprint.layers import PROJECT_LAYER_FOLDER
from app.blueprint.skill_install import SOURCE_FILE
from app.core.skipped_folders import SKIPPED_FOLDERS
from app.outline.parsers import ParserOutliner
from app.outline.symbols import Outliner, render_outline
from app.targets.base import TargetFiles

MAX_READ_BYTES = 512 * 1024
# An outline is short whatever the file's size, so it may cover files too large to read whole.
MAX_OUTLINE_BYTES = 4 * 1024 * 1024
# The reading window: most reads need one part of a file, and what was read stays in the context.
DEFAULT_READ_LINES = 400
MAX_READ_LINES = 2000
# Longer lines (minified code, data) are cut so one line cannot fill the context.
MAX_LINE_CHARS = 2000
# Where outlines come from; a language server's document symbols can take this place.
OUTLINER: Outliner = ParserOutliner()


async def list_files(run: AgentRun, arguments: dict[str, Any]) -> str:
    target = files(run)
    folder = target.check(str(arguments.get("path") or "."))
    recursive = bool(arguments.get("recursive", False))
    found = await target.stat(folder)
    if found is None or not found.is_dir:
        raise ToolError(f"{arguments.get('path')} is not a folder.")
    walked = await target.walk(folder, recursive, SKIPPED_FOLDERS, 1000)
    entries = [entry.path + "/" if entry.is_dir else entry.path for entry in walked]
    if not entries:
        return "The folder is empty."
    shown = sorted(entries)[:1000]
    suffix = f"\n[{len(entries) - 1000} more entries not shown.]" if len(entries) > 1000 else ""
    return "\n".join(shown) + suffix


async def read_file(run: AgentRun, arguments: dict[str, Any]) -> str:
    target = files(run)
    path = target.check(text_argument(arguments, "path"))
    found = await target.stat(path)
    if found is None or found.is_dir:
        raise ToolError(f"{arguments['path']} does not exist or is not a file.")
    outline = bool(arguments.get("outline"))
    if outline and found.size > MAX_OUTLINE_BYTES:
        raise ToolError("The file is over 4 MB, too large for an outline. Read it in parts with offset and limit.")
    if not outline and found.size > MAX_READ_BYTES and "offset" not in arguments:
        raise ToolError(
            "The file is larger than 512 KB. Read it in parts with offset and limit, or list its classes and functions "
            "with outline=true."
        )
    data = await target.read_bytes(path)
    text = data.decode("utf-8", errors="replace").replace("\r\n", "\n")
    remember_file(run, path, data)
    if await is_outside_file(run, target, path):
        note_outside(run, f"file {path}", text)
    if outline:
        outliner = run.code_intel.outliner(OUTLINER) if run.code_intel is not None else OUTLINER
        return truncate(render_outline(path, len(text.splitlines()), await outliner.outline(path, text)))
    offset = max(int(arguments.get("offset") or 1), 1)
    limit = min(max(int(arguments.get("limit") or DEFAULT_READ_LINES), 1), MAX_READ_LINES)
    lines = text.splitlines()
    window = numbered_window(lines, offset, limit)
    if not run.state.context_savings:
        return window
    shown = shown_range(lines, offset, limit)
    if shown is None:
        return window
    key = (path, *shown)
    digest = text_digest(window)
    note = repeat_note(run.state.read_ranges.get(key), path, shown[0], shown[1], digest)
    if note is not None:
        return note
    run.state.reads += 1
    run.state.read_ranges[key] = (digest, run.state.reads)
    return window


def shown_range(lines: list[str], offset: int, limit: int) -> tuple[int, int] | None:
    """First and last line number numbered_window shows for this request; None when it shows no line."""
    rows = window_rows(lines, offset, limit)
    return (offset, offset + len(rows) - 1) if rows else None


def numbered_window(lines: list[str], offset: int, limit: int) -> str:
    """Lines offset to offset + limit - 1 with their numbers, as many as fit in one output, and a note about the rest."""
    rows = window_rows(lines, offset, limit)
    if not rows:
        return "The file is empty." if not lines else f"The file has only {len(lines)} lines."
    return "\n".join(rows) + window_note(offset, offset + len(rows) - 1, len(lines))


def window_rows(lines: list[str], offset: int, limit: int) -> list[str]:
    """The numbered rows of a window, cut where one output is full."""
    rows: list[str] = []
    size = 0
    for index, line in enumerate(lines[offset - 1 : offset - 1 + limit]):
        row = f"{offset + index:>6}\t{cut_line(line)}"
        if rows and size + len(row) > MAX_OUTPUT_CHARS - 500:
            break
        rows.append(row)
        size += len(row) + 1
    return rows


def window_note(first: int, last: int, total: int) -> str:
    """Which lines a read left out and how to read them; nothing when it showed the whole file."""
    if first == 1 and last == total:
        return ""
    left_out = ([f"1–{first - 1}"] if first > 1 else []) + ([f"{last + 1}–{total}"] if last < total else [])
    more = f" Continue with offset={last + 1}." if last < total else ""
    return (
        f"\n\n[Lines {first}–{last} of {total} shown; lines {' and '.join(left_out)} left out.{more} Read any part "
        f"with offset and limit (at most {MAX_READ_LINES} lines); outline=true lists the file's classes and functions.]"
    )


def cut_line(line: str) -> str:
    return line if len(line) <= MAX_LINE_CHARS else line[:MAX_LINE_CHARS] + f" [line cut: {len(line)} characters]"


async def is_outside_file(run: AgentRun, target: TargetFiles, path: str) -> bool:
    """A file that came from outside: imported from an attachment in this run, or part of a skill from GitHub."""
    if path in run.state.outside_files:
        return True
    parts = path.split("/")
    if len(parts) >= 3 and parts[0] == PROJECT_LAYER_FOLDER and parts[1] == "skills":
        return await target.stat("/".join([*parts[:3], SOURCE_FILE])) is not None
    return False


async def search(run: AgentRun, arguments: dict[str, Any]) -> str:
    try:
        pattern = re.compile(text_argument(arguments, "pattern"))
    except re.error as error:
        raise ToolError(f"Invalid regular expression: {error}") from error
    target = files(run)
    folder = target.check(str(arguments.get("path") or "."))
    glob = str(arguments.get("glob") or "*")
    matches = await target.search(pattern.pattern, folder, glob, 200, SKIPPED_FOLDERS)
    return "\n".join(matches) if matches else "No matches."


async def write_file(run: AgentRun, arguments: dict[str, Any]) -> str:
    target = files(run)
    path = target.check(text_argument(arguments, "path"), for_write=True)
    content = text_argument(arguments, "content", required=False)
    found = await target.stat(path)
    existed = found is not None
    if found is not None and not found.is_dir:
        refuse_if_changed(run, path, await target.read_bytes(path))
    data = content.encode("utf-8")
    await target.write_bytes(path, data)
    remember_file(run, path, data)
    run.state.changed_files.add(path)
    done = f"{'Replaced' if existed else 'Created'} {path} ({len(content.splitlines())} lines)."
    return done + await diagnostics_after_write(run, path, content)


async def edit_file(run: AgentRun, arguments: dict[str, Any]) -> str:
    target = files(run)
    path = target.check(text_argument(arguments, "path"), for_write=True)
    old = text_argument(arguments, "old_string")
    new = text_argument(arguments, "new_string", required=False)
    found = await target.stat(path)
    if found is None or found.is_dir:
        raise ToolError(f"{arguments['path']} does not exist.")
    current = await target.read_bytes(path)
    refuse_if_changed(run, path, current)
    # Universal newlines, as reading text always did: old_string from the model uses \n.
    text = current.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    count = text.count(old)
    if count == 0:
        raise ToolError("old_string was not found. Read the file again and copy the text exactly.")
    if count > 1 and not arguments.get("replace_all"):
        raise ToolError(f"old_string occurs {count} times. Add surrounding lines to make it unique, or set replace_all.")
    changed = text.replace(old, new)
    data = changed.encode("utf-8")
    await target.write_bytes(path, data)
    remember_file(run, path, data)
    run.state.changed_files.add(path)
    done = f"Edited {path} ({count} replacement{'s' if count > 1 else ''})."
    return done + await diagnostics_after_write(run, path, changed)


async def diagnostics_after_write(run: AgentRun, path: str, text: str) -> str:
    """The file's new diagnostics when a language server runs for it (app/lsp/service.py), else nothing."""
    return await run.code_intel.after_write(path, text) if run.code_intel is not None else ""


FILE_TOOLS = [
    Tool(
        "list_files",
        "List the files and folders in a folder of the project. Paths are relative to the project folder.",
        schema({"path": STRING, "recursive": {"type": "boolean"}}, []),
        "auto",
        list_files,
        read_only=True,
    ),
    Tool(
        "read_file",
        "Read a text file of the project, with line numbers: the first 400 lines, or limit lines (at most 2000) from "
        "offset (the first line). outline=true lists a code or Markdown file's classes, functions and headings with "
        "their line ranges instead, to find the part to read. Read a file again before editing it when it may have "
        "changed.",
        schema(
            {
                "path": STRING,
                "offset": {"type": "integer"},
                "limit": {"type": "integer"},
                "outline": {"type": "boolean"},
            },
            ["path"],
        ),
        "auto",
        read_file,
        read_only=True,
    ),
    Tool(
        "search",
        "Search the project's text files for a regular expression. Optionally limit to a folder and a file glob.",
        schema({"pattern": STRING, "path": STRING, "glob": STRING}, ["pattern"]),
        "auto",
        search,
        read_only=True,
    ),
    Tool(
        "write_file",
        "Create a file in the project or replace its whole content.",
        schema({"path": STRING, "content": STRING}, ["path", "content"]),
        "auto",
        write_file,
    ),
    Tool(
        "edit_file",
        "Replace an exact, unique piece of text in a project file. Read the file first and copy old_string exactly.",
        schema(
            {"path": STRING, "old_string": STRING, "new_string": STRING, "replace_all": {"type": "boolean"}},
            ["path", "old_string", "new_string"],
        ),
        "auto",
        edit_file,
    ),
]
