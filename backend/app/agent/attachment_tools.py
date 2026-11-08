"""Tools for the attachments of a conversation: look inside archives, read files, import into a project.

Reading is always allowed; importing writes into the project folder and asks before it overwrites a file.
"""

import base64
import os
import re
import shutil
from functools import partial
from pathlib import Path, PurePosixPath
from typing import Any

import anyio

from app.agent.runs import AgentRun, AttachmentRef
from app.agent.toolkit import STRING, Tool, ToolError, ToolOutput, schema, text_argument, truncate
from app.attachments.files import (
    UNPACKED_KINDS,
    AttachmentError,
    classify,
    extract_office_text,
    human_size,
    image_for_model,
    original_file,
    pdf_text,
    scaled_image,
)

MAX_LISTED = 1000
MAX_MATCHES = 200
MAX_SEARCH_FILE_BYTES = 2 * 1024 * 1024


def find_attachment(run: AgentRun, value: str) -> AttachmentRef:
    if not run.attachments:
        raise ToolError("This conversation has no attachments.")
    if not value and len(run.attachments) == 1:
        return run.attachments[0]
    for attachment in run.attachments:
        if value == str(attachment.id) or value.lower() == attachment.name.lower():
            return attachment
    names = ", ".join(f"{attachment.id} ({attachment.name})" for attachment in run.attachments)
    raise ToolError(f"No attachment '{value}'. Attachments: {names}.")


def archive_root(attachment: AttachmentRef) -> Path:
    """The unpacked files of a zip or a folder attachment."""
    if attachment.kind not in UNPACKED_KINDS:
        raise ToolError(f"{attachment.name} is not an archive or a folder; read it without a path.")
    return (attachment.folder / "unpacked").resolve()


def archive_file(attachment: AttachmentRef, path: str) -> Path:
    root = archive_root(attachment)
    target = (root / path).resolve()
    if not target.is_relative_to(root):
        raise ToolError(f"{path} is outside the archive.")
    if not target.exists():
        raise ToolError(f"{path} does not exist in {attachment.name}.")
    return target


def numbered(text: str, arguments: dict[str, Any]) -> str:
    lines = text.splitlines()
    offset = max(int(arguments.get("offset") or 1), 1)
    limit = min(max(int(arguments.get("limit") or 2000), 1), 2000)
    selected = lines[offset - 1 : offset - 1 + limit]
    if not selected:
        return "The file is empty." if not lines else f"The file has only {len(lines)} lines."
    body = "\n".join(f"{offset + index:>6}\t{line}" for index, line in enumerate(selected))
    more = f"\n[Lines {offset + len(selected)}–{len(lines)} not shown.]" if offset - 1 + limit < len(lines) else ""
    return truncate(body + more)


def image_output(name: str, data: bytes, media_type: str) -> ToolOutput:
    scaled = scaled_image(data)
    if scaled:
        data, media_type = scaled
    return ToolOutput.with_images(f"Image {name}:", [(media_type, base64.b64encode(data).decode())])


def read_any_file(file: Path, arguments: dict[str, Any]) -> str:
    """Read a file from inside an archive by what it is."""
    with file.open("rb") as handle:
        head = handle.read(4096)
    kind, media_type = classify(file.name, head)
    if kind == "image":
        return image_output(file.name, file.read_bytes(), media_type)
    if kind == "pdf":
        text, pages = pdf_text(file)
        return numbered(f"PDF with {pages} pages.\n\n{text}", arguments)
    if kind == "office":
        return numbered(extract_office_text(file), arguments)
    if kind == "text":
        return numbered(file.read_text(encoding="utf-8", errors="replace"), arguments)
    return f"{file.name} is a binary file ({human_size(file.stat().st_size)}); it cannot be read as text."


def entries_below(root: Path, folder: Path) -> list[str]:
    entries = []
    for current, folders, files in os.walk(folder):
        folders.sort()
        base = Path(current)
        for name in sorted(files):
            file = base / name
            entries.append(f"{file.relative_to(root).as_posix()} ({human_size(file.stat().st_size)})")
            if len(entries) > MAX_LISTED:
                return entries
    return entries


async def list_attachment(run: AgentRun, arguments: dict[str, Any]) -> str:
    value = str(arguments.get("attachment") or "")
    if not value and len(run.attachments) != 1:
        if not run.attachments:
            return "This conversation has no attachments."
        return "\n".join(
            f"{attachment.id}: {attachment.name} ({attachment.kind})" for attachment in run.attachments
        )
    attachment = find_attachment(run, value)
    if attachment.kind not in UNPACKED_KINDS:
        return f"{attachment.id}: {attachment.name} ({attachment.kind}, {attachment.media_type})"
    root = archive_root(attachment)
    folder = archive_file(attachment, str(arguments.get("path") or "."))
    if folder.is_file():
        return f"{folder.relative_to(root).as_posix()} ({human_size(folder.stat().st_size)})"
    entries = await anyio.to_thread.run_sync(entries_below, root, folder)
    if not entries:
        return "The folder is empty."
    suffix = f"\n[More than {MAX_LISTED} files; list a sub folder.]" if len(entries) > MAX_LISTED else ""
    return "\n".join(entries[:MAX_LISTED]) + suffix


async def read_attachment(run: AgentRun, arguments: dict[str, Any]) -> str:
    attachment = find_attachment(run, str(arguments.get("attachment") or ""))
    path = str(arguments.get("path") or "")
    try:
        if attachment.kind in UNPACKED_KINDS:
            if not path:
                what = "a folder" if attachment.kind == "folder" else "an archive"
                raise ToolError(f"{attachment.name} is {what}. Give the path of a file inside it.")
            file = archive_file(attachment, path)
            if file.is_dir():
                raise ToolError(f"{path} is a folder. Use list_attachment to see its files.")
            return await anyio.to_thread.run_sync(read_any_file, file, arguments)
        if attachment.kind == "image":
            data, media_type = image_for_model(attachment.folder, attachment.media_type)
            return image_output(attachment.name, data, media_type)
        if attachment.kind in ("pdf", "office"):
            extracted = attachment.folder / "extracted.md"
            return numbered(await anyio.to_thread.run_sync(partial(extracted.read_text, encoding="utf-8")), arguments)
        if attachment.kind == "text":
            read = partial(original_file(attachment.folder).read_text, encoding="utf-8", errors="replace")
            text = await anyio.to_thread.run_sync(read)
            return numbered(text, arguments)
        return f"{attachment.name} is a binary file; it cannot be read as text."
    except AttachmentError as error:
        raise ToolError(str(error)) from error


def searchable_files(attachment: AttachmentRef) -> list[tuple[str, Path]]:
    """(display path, file) pairs whose text can be searched."""
    if attachment.kind in UNPACKED_KINDS:
        root = archive_root(attachment)
        return [
            (f"{attachment.name}/{file.relative_to(root).as_posix()}", file)
            for file in sorted(root.rglob("*"))
            if file.is_file()
        ]
    if attachment.kind in ("pdf", "office"):
        return [(attachment.name, attachment.folder / "extracted.md")]
    if attachment.kind == "text":
        return [(attachment.name, original_file(attachment.folder))]
    return []


async def search_attachment(run: AgentRun, arguments: dict[str, Any]) -> str:
    try:
        pattern = re.compile(text_argument(arguments, "pattern"))
    except re.error as error:
        raise ToolError(f"Invalid regular expression: {error}") from error
    value = str(arguments.get("attachment") or "")
    attachments = [find_attachment(run, value)] if value else run.attachments
    glob = str(arguments.get("glob") or "*")

    def scan() -> list[str]:
        matches: list[str] = []
        for attachment in attachments:
            for shown, file in searchable_files(attachment):
                if not PurePosixPath(shown).match(glob) or file.stat().st_size > MAX_SEARCH_FILE_BYTES:
                    continue
                try:
                    text = file.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                for number, line in enumerate(text.splitlines(), 1):
                    if pattern.search(line):
                        matches.append(f"{shown}:{number}: {line.strip()[:300]}")
                        if len(matches) >= MAX_MATCHES:
                            return matches
        return matches

    matches = await anyio.to_thread.run_sync(scan)
    return "\n".join(matches) if matches else "No matches."


def import_plan(run: AgentRun, arguments: dict[str, Any]) -> list[tuple[Path, Path]]:
    """(source, target) pairs for an import, checked to stay inside the archive and the project."""
    if run.project_folder is None:
        raise ToolError("This conversation has no project to import into.")
    attachment = find_attachment(run, str(arguments.get("attachment") or ""))
    project = run.project_folder.resolve()
    target = (project / str(arguments.get("target") or ".")).resolve()
    if not target.is_relative_to(project) or ".git" in target.relative_to(project).parts:
        raise ToolError("The target must be inside the project and outside .git.")
    if attachment.kind not in UNPACKED_KINDS:
        source = original_file(attachment.folder)
        destination = target / source.name if target.is_dir() or not target.suffix else target
        return [(source, destination)]
    root = archive_root(attachment)
    source = archive_file(attachment, str(arguments.get("path") or "."))
    if source.is_file():
        destination = target / source.name if target.is_dir() or not target.suffix else target
        return [(source, destination)]
    return [(file, target / file.relative_to(source)) for file in sorted(source.rglob("*")) if file.is_file()]


def overwrites(run: AgentRun, arguments: dict[str, Any]) -> bool:
    try:
        return any(target.exists() for _, target in import_plan(run, arguments))
    except (ToolError, AttachmentError):
        return False


async def import_attachment(run: AgentRun, arguments: dict[str, Any]) -> str:
    try:
        plan = import_plan(run, arguments)
    except AttachmentError as error:
        raise ToolError(str(error)) from error
    project = run.project_folder.resolve() if run.project_folder else Path()

    def copy() -> tuple[int, int]:
        created = replaced = 0
        for source, target in plan:
            existed = target.exists()
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            replaced += existed
            created += not existed
        return created, replaced

    created, replaced = await anyio.to_thread.run_sync(copy)
    run.state.outside_files.update(target.relative_to(project).as_posix() for _, target in plan)
    shown = ", ".join(target.relative_to(project).as_posix() for _, target in plan[:10])
    more = f" and {len(plan) - 10} more" if len(plan) > 10 else ""
    return f"Imported {len(plan)} files ({created} new, {replaced} replaced): {shown}{more}."


ATTACHMENT = {"type": "string", "description": "The attachment's id or file name."}

ATTACHMENT_TOOLS = [
    Tool(
        "list_attachment",
        "List the conversation's attachments, or the files inside a zip or folder attachment (optionally below a "
        "path).",
        schema({"attachment": ATTACHMENT, "path": STRING}, []),
        "auto",
        list_attachment,
        scope="attachments",
        read_only=True,
    ),
    Tool(
        "read_attachment",
        "Read an attachment, or a file inside a zip or folder attachment by its path. Text comes back with line numbers "
        "(use offset and limit for long files); images come back as images; PDF and Office files as their text.",
        schema(
            {"attachment": ATTACHMENT, "path": STRING, "offset": {"type": "integer"}, "limit": {"type": "integer"}},
            ["attachment"],
        ),
        "auto",
        read_attachment,
        scope="attachments",
        read_only=True,
    ),
    Tool(
        "search_attachment",
        "Search the text of the attachments (including files inside zips and folders) for a regular expression.",
        schema({"pattern": STRING, "attachment": ATTACHMENT, "glob": STRING}, ["pattern"]),
        "auto",
        search_attachment,
        scope="attachments",
        read_only=True,
    ),
    Tool(
        "import_attachment",
        "Copy an attachment, or a file or folder inside a zip or folder attachment, into the project. 'target' is a "
        "folder or file path in the project. Overwriting existing files waits for the user's approval.",
        schema({"attachment": ATTACHMENT, "path": STRING, "target": STRING}, ["attachment"]),
        "conditional",
        import_attachment,
        describe=lambda arguments: (
            f"Import {arguments.get('path') or arguments.get('attachment')} into "
            f"{arguments.get('target') or 'the project root'}, replacing existing files"
        ),
        scope="project_attachments",
        needs_approval=overwrites,
    ),
]
