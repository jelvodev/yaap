"""The code tools: definitions, references, diagnostics, renaming and document symbols from the project's language
servers (the servers in app/lsp/).

They exist in a run only when a server for one of its project's languages can run where its commands run
(`RunServices.code_intel`). A position is the file's path, the line as `read_file` numbers it, and the symbol's name
as it is written on that line: models copy a name and a line number from what they read far more reliably than they
count columns. YAAP finds the name on that line (or within two lines of it) and turns it into the protocol's position.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.agent.file_tools import FILE_TOOLS, MAX_READ_BYTES, SKIPPED_FOLDERS
from app.agent.guard import refuse_if_changed, remember_file
from app.agent.runs import AgentRun
from app.agent.tool_context import files
from app.agent.toolkit import STRING, Tool, ToolError, schema, text_argument, truncate
from app.lsp.client import LanguageClient
from app.lsp.connection import LspError
from app.lsp.paths import PathMap
from app.lsp.results import Location, diagnostic_rows, locations
from app.lsp.service import CodeIntel, code_intel_for, document_outline
from app.lsp.servers import ServerSpec
from app.lsp.text import (
    PositionError,
    Resolved,
    TextEdit,
    apply_edits,
    index_from_utf16,
    line_text,
    resolve_position,
    split_lines,
)
from app.outline.symbols import render_outline
from app.targets.ssh import SshTarget

# How long the diagnostics tool waits for a server that may still be analysing the project.
DIAGNOSTICS_TOOL_WAIT = 10.0
MAX_DIAGNOSTICS = 50
MAX_LOCATIONS = 100
# Result rows show the line's text for locations in at most this many files.
MAX_FILES_READ = 40
MAX_NAME_CHARS = 200
EDIT_PERMISSION = next(tool.permission for tool in FILE_TOOLS if tool.name == "edit_file")


def code_intel_for_run(project_folder: Path | None, runtime: SshTarget | None) -> CodeIntel | None:
    """The code intelligence of a run at its start (core/reply_run.py)."""
    return code_intel_for(project_folder, runtime, frozenset(SKIPPED_FOLDERS))


@contextmanager
def as_tool_errors() -> Iterator[None]:
    try:
        yield
    except (LspError, PositionError) as error:
        raise ToolError(str(error)) from error


def intel(run: AgentRun) -> CodeIntel:
    if run.code_intel is None:
        raise ToolError("No language server runs for this project.")
    return run.code_intel


async def read_text(run: AgentRun, path: str) -> tuple[str, str]:
    """The project file's checked relative path and text, as it is on disk now."""
    target = files(run)
    relative = target.check(path)
    found = await target.stat(relative)
    if found is None or found.is_dir:
        raise ToolError(f"{path} does not exist or is not a file.")
    if found.size > MAX_READ_BYTES:
        raise ToolError(f"{path} is larger than 512 KB; the code tools work on smaller files.")
    data = await target.read_bytes(relative)
    remember_file(run, relative, data)
    return relative, data.decode("utf-8", errors="replace")


def line_argument(arguments: dict[str, Any]) -> int:
    value = arguments.get("line")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise ToolError("'line' must be the line number, as read_file shows it.")


@dataclass(frozen=True)
class Located:
    client: LanguageClient
    spec: ServerSpec
    path: str
    uri: str
    symbol: str
    position: Resolved
    asked_line: int

    def params(self) -> dict[str, Any]:
        return {"textDocument": {"uri": self.uri}, "position": self.position.lsp()}

    def note(self) -> str:
        """Says so when the symbol was found on a line near the one asked for."""
        if self.position.used_line == self.asked_line:
            return ""
        return f" (found on line {self.position.used_line}, not {self.asked_line})"


async def locate(run: AgentRun, arguments: dict[str, Any]) -> Located:
    """The file's server, with the file's current text sent, and the position of the symbol on the line."""
    code = intel(run)
    path, text = await read_text(run, text_argument(arguments, "path"))
    line = line_argument(arguments)
    symbol = text_argument(arguments, "symbol")
    with as_tool_errors():
        position = resolve_position(text, line, symbol)
        client, spec = await code.client(path)
        uri, _, _ = await client.sync(path, text)
    return Located(client, spec, path, uri, symbol.strip(), position, line)


async def location_rows(run: AgentRun, paths: PathMap, found: list[Location]) -> list[str]:
    """One row per location, `path:line:column: text`, 1-based; outside the project with the server's path."""
    texts: dict[str, list[str] | None] = {}
    rows: list[str] = []
    for location in found[:MAX_LOCATIONS]:
        relative = paths.relative(location.uri)
        if relative is None:
            where = f"{paths.label(location.uri)}:{location.line + 1}:{location.character + 1}"
            rows.append(f"{where} (outside the project)")
            continue
        if relative not in texts and len(texts) < MAX_FILES_READ:
            try:
                texts[relative] = split_lines((await read_text(run, relative))[1])
            except ToolError:
                texts[relative] = None
        lines = texts.get(relative)
        if not lines or location.line >= len(lines):
            rows.append(f"{relative}:{location.line + 1}:{location.character + 1}")
            continue
        column = index_from_utf16(lines[location.line], location.character) + 1
        rows.append(f"{relative}:{location.line + 1}:{column}: {line_text(lines, location.line)}")
    if len(found) > MAX_LOCATIONS:
        rows.append(f"[{len(found) - MAX_LOCATIONS} more not shown.]")
    return rows


async def find_definition(run: AgentRun, arguments: dict[str, Any]) -> str:
    at = await locate(run, arguments)
    with as_tool_errors():
        found = locations(await at.client.request("textDocument/definition", at.params()))
    where = f"{at.path}:{at.position.used_line}"
    if not found:
        return f"{at.spec.name} found no definition of '{at.symbol}' at {where}{at.note()}."
    rows = await location_rows(run, intel(run).place.paths, found)
    return truncate(f"Definition of '{at.symbol}' at {where}{at.note()} ({at.spec.name}):\n" + "\n".join(rows))


async def find_references(run: AgentRun, arguments: dict[str, Any]) -> str:
    at = await locate(run, arguments)
    params = {**at.params(), "context": {"includeDeclaration": True}}
    with as_tool_errors():
        found = locations(await at.client.request("textDocument/references", params))
    where = f"{at.path}:{at.position.used_line}"
    if not found:
        return f"{at.spec.name} found no references to '{at.symbol}' at {where}{at.note()}."
    rows = await location_rows(run, intel(run).place.paths, found)
    count = len(found)
    header = f"{count} reference{'s' if count > 1 else ''} to '{at.symbol}' at {where}{at.note()} ({at.spec.name}):"
    return truncate(header + "\n" + "\n".join(rows))


async def diagnostics(run: AgentRun, arguments: dict[str, Any]) -> str:
    code = intel(run)
    path, text = await read_text(run, text_argument(arguments, "path"))
    with as_tool_errors():
        client, spec = await code.client(path)
        after = client.generation
        uri, version, sent = await client.sync(path, text)
        published = (
            await client.wait_diagnostics(uri, version, after, DIAGNOSTICS_TOOL_WAIT) if sent else client.current(uri)
        )
    if published is None:
        return (
            f"{spec.name} sent no diagnostics for {path} within {DIAGNOSTICS_TOOL_WAIT:g} seconds; it may still be "
            "analysing the project. Try again shortly."
        )
    if not published.diagnostics:
        return f"{spec.name}: no problems in {path}."
    count = len(published.diagnostics)
    rows = diagnostic_rows(published.diagnostics, MAX_DIAGNOSTICS)
    return f"{spec.name} reports {count} problem{'s' if count > 1 else ''} in {path} (line:column):\n" + "\n".join(rows)


async def document_symbols(run: AgentRun, arguments: dict[str, Any]) -> str:
    code = intel(run)
    path, text = await read_text(run, text_argument(arguments, "path"))
    with as_tool_errors():
        client, _ = await code.client(path)
        outline = await document_outline(client, path, text, 30.0)
    return truncate(render_outline(path, len(text.splitlines()), outline))


def workspace_changes(edit: Any, paths: PathMap) -> dict[str, list[TextEdit]]:
    """A WorkspaceEdit as text edits per project file. Refuses creating, renaming or deleting files, and changes
    outside the project."""
    if not isinstance(edit, dict):
        return {}
    by_uri: dict[str, list[dict[str, Any]]] = {}
    for uri, edits in (edit.get("changes") or {}).items():
        by_uri.setdefault(str(uri), []).extend(edits or [])
    for change in edit.get("documentChanges") or []:
        if not isinstance(change, dict) or "kind" in change:
            raise ToolError("The rename would create, rename or delete files; YAAP applies only changes inside files.")
        uri = str((change.get("textDocument") or {}).get("uri") or "")
        by_uri.setdefault(uri, []).extend(change.get("edits") or [])
    changes: dict[str, list[TextEdit]] = {}
    for uri, edits in by_uri.items():
        relative = paths.relative(uri)
        if relative is None:
            raise ToolError(
                f"The rename would change {paths.label(uri)}, which is outside the project; nothing was changed."
            )
        changes.setdefault(relative, []).extend(TextEdit.from_lsp(item) for item in edits if isinstance(item, dict))
    return {relative: edits for relative, edits in changes.items() if edits}


def new_name_argument(arguments: dict[str, Any]) -> str:
    name = text_argument(arguments, "new_name").strip()
    if not name or len(name) > MAX_NAME_CHARS or any(char.isspace() for char in name):
        raise ToolError("'new_name' must be the new identifier, without spaces.")
    return name


async def rename_symbol(run: AgentRun, arguments: dict[str, Any]) -> str:
    new_name = new_name_argument(arguments)
    at = await locate(run, arguments)
    with as_tool_errors():
        edit = await at.client.request("textDocument/rename", {**at.params(), "newName": new_name})
    changes = workspace_changes(edit, intel(run).place.paths)
    if not changes:
        return f"{at.spec.name} found nothing to rename for '{at.symbol}' at {at.path}:{at.position.used_line}."
    target = files(run)
    planned: list[tuple[str, str, int]] = []
    # Every file is read and changed in memory first, so a file that cannot be changed leaves all of them as they were.
    for relative, edits in sorted(changes.items()):
        path = target.check(relative, for_write=True)
        current = await target.read_bytes(path)
        refuse_if_changed(run, path, current)
        try:
            text = current.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ToolError(f"{path} is not UTF-8 text; nothing was renamed.") from error
        with as_tool_errors():
            planned.append((path, apply_edits(text, edits), len(edits)))
    for path, text, _ in planned:
        data = text.encode("utf-8")
        await target.write_bytes(path, data)
        remember_file(run, path, data)
        run.state.changed_files.add(path)
        with as_tool_errors():
            await at.client.sync(path, text)
    total = sum(count for _, _, count in planned)
    rows = [f"  {path} ({count} change{'s' if count > 1 else ''})" for path, _, count in planned]
    files_count = len(planned)
    header = (
        f"Renamed '{at.symbol}' to '{new_name}' with {at.spec.name}{at.note()}: "
        f"{total} change{'s' if total > 1 else ''} "
        f"in {files_count} file{'s' if files_count > 1 else ''}."
    )
    return "\n".join([header, *rows])


def rename_asks(run: AgentRun, arguments: dict[str, Any]) -> bool:
    """A rename changes files, so it asks exactly when edit_file asks (the user's choice in Settings)."""
    return run.policy.permission_for("edit_file", EDIT_PERMISSION) == "ask"


def describe_rename(arguments: dict[str, Any]) -> str:
    symbol, new_name = arguments.get("symbol") or "?", arguments.get("new_name") or "?"
    return f"Rename {symbol} to {new_name} ({arguments.get('path')}:{arguments.get('line')}) everywhere it is used"


POSITION = {"path": STRING, "line": {"type": "integer"}, "symbol": STRING}
POSITION_HELP = "Give the file, the line as read_file numbers it, and the symbol's name as written on that line."

CODE_TOOLS = [
    Tool(
        "find_definition",
        f"Find where a symbol is defined, with the project's language server. {POSITION_HELP}",
        schema(POSITION, ["path", "line", "symbol"]),
        "auto",
        find_definition,
        read_only=True,
    ),
    Tool(
        "find_references",
        f"Find every use of a symbol across the project, with the language server. {POSITION_HELP}",
        schema(POSITION, ["path", "line", "symbol"]),
        "auto",
        find_references,
        read_only=True,
    ),
    Tool(
        "diagnostics",
        "The language server's errors and warnings for a file of the project, such as type errors and unknown names.",
        schema({"path": STRING}, ["path"]),
        "auto",
        diagnostics,
        read_only=True,
    ),
    Tool(
        "rename_symbol",
        f"Rename a symbol everywhere it is used, across files, with the language server. {POSITION_HELP} new_name is "
        "the new identifier.",
        schema({**POSITION, "new_name": STRING}, ["path", "line", "symbol", "new_name"]),
        "conditional",
        rename_symbol,
        describe=describe_rename,
        needs_approval=rename_asks,
    ),
    Tool(
        "document_symbols",
        "List a file's classes, functions and methods with their line ranges, from the language server.",
        schema({"path": STRING}, ["path"]),
        "auto",
        document_symbols,
        read_only=True,
    ),
]
