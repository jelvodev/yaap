"""A project's files in the interface: the Files tab and the editor.

Everything goes through the project's target, like the agent's file tools: YAAP's container, or the project's folder
on a computer over SSH. Saving checks the hash of the version that was opened and takes a checkpoint first.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import quote

import asyncssh
from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field

from app.api.dependencies import CipherDependency, ProjectDependency, SessionDependency, WorkspaceDirDependency
from app.checkpoints.service import CheckpointStore
from app.documents.service import notify_changed
from app.files.editing import INLINE_TYPES, FileChanged, open_file, raw_file, save_file
from app.files.service import ProjectFiles, find_by_name, find_in_content, list_folder, name_of, project_files
from app.projects.service import ProjectError
from app.targets.base import FileEntry, TargetError

router = APIRouter(tags=["files"])


class FilesLocationOut(BaseModel):
    kind: Literal["container", "ssh"]
    # YAAP's container, or the computer's name.
    label: str
    # Whether a save takes a checkpoint first (only in YAAP's container).
    checkpoints: bool


class FileEntryOut(BaseModel):
    path: str
    name: str
    is_dir: bool
    size: int
    modified: datetime | None


class FolderOut(BaseModel):
    location: FilesLocationOut
    path: str
    entries: list[FileEntryOut]
    # More entries than are shown.
    truncated: bool


class FileMatchOut(BaseModel):
    path: str
    line: int
    text: str


class FileSearchOut(BaseModel):
    mode: Literal["name", "content"]
    entries: list[FileEntryOut] = []
    matches: list[FileMatchOut] = []
    truncated: bool = False


class FileContentOut(BaseModel):
    path: str
    name: str
    size: int
    modified: datetime | None
    kind: Literal["text", "image", "pdf", "binary", "too_large"]
    media_type: str
    # SHA-256 of the content as opened; a save sends it back so a change since is noticed.
    hash: str | None
    text: str | None
    read_only: bool
    note: str | None
    location: FilesLocationOut


class FileSaveIn(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    text: str
    # The hash of the version the editor opened; null for a new file.
    base_hash: str | None


class FileSavedOut(BaseModel):
    path: str
    size: int
    modified: datetime | None
    hash: str
    # The checkpoint taken just before the save; null on a computer over SSH.
    checkpoint_id: int | None


def entry_out(entry: FileEntry) -> FileEntryOut:
    return FileEntryOut(
        path=entry.path,
        name=name_of(entry.path),
        is_dir=entry.is_dir,
        size=entry.size,
        modified=modified_at(entry.modified),
    )


def modified_at(timestamp: float | None) -> datetime | None:
    return datetime.fromtimestamp(timestamp, UTC) if timestamp is not None else None


def location_out(located: ProjectFiles) -> FilesLocationOut:
    return FilesLocationOut(
        kind=located.location,  # type: ignore[arg-type]  # a plain string in the service; the schema narrows it
        label=located.label,
        checkpoints=located.folder is not None,
    )


@asynccontextmanager
async def located_files(
    session: SessionDependency, cipher: CipherDependency, workspace_dir: WorkspaceDirDependency, project: ProjectDependency
) -> AsyncIterator[ProjectFiles]:
    """The project's files; a refused path or an unreachable computer becomes a readable error."""
    try:
        located = project_files(session, cipher, workspace_dir, project)
    except (TargetError, ProjectError) as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    try:
        yield located
    except FileChanged as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except TargetError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    except (OSError, asyncssh.Error) as error:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"The files could not be read: {error}") from error


@router.get("/projects/{project_id}/files", response_model=FolderOut)
async def list_files(
    project: ProjectDependency,
    session: SessionDependency,
    cipher: CipherDependency,
    workspace_dir: WorkspaceDirDependency,
    path: str = ".",
) -> FolderOut:
    """One folder level of the project, folders first, without .git and dependency folders."""
    async with located_files(session, cipher, workspace_dir, project) as located:
        folder, entries, truncated = await list_folder(located.files, path)
        return FolderOut(
            location=location_out(located),
            path=folder,
            entries=[entry_out(entry) for entry in entries],
            truncated=truncated,
        )


@router.get("/projects/{project_id}/files/search", response_model=FileSearchOut)
async def search_files(
    project: ProjectDependency,
    session: SessionDependency,
    cipher: CipherDependency,
    workspace_dir: WorkspaceDirDependency,
    query: str = Query(min_length=1, max_length=200),
    mode: Literal["name", "content"] = "name",
) -> FileSearchOut:
    """Files by name, or lines by content (the agent's search), case-insensitive."""
    async with located_files(session, cipher, workspace_dir, project) as located:
        if mode == "name":
            entries, truncated = await find_by_name(located.files, query)
            return FileSearchOut(mode=mode, entries=[entry_out(entry) for entry in entries], truncated=truncated)
        matches, truncated = await find_in_content(located.files, query)
        return FileSearchOut(
            mode=mode,
            matches=[FileMatchOut(path=path, line=line, text=text) for path, line, text in matches],
            truncated=truncated,
        )


@router.get("/projects/{project_id}/files/content", response_model=FileContentOut)
async def read_file(
    project: ProjectDependency,
    session: SessionDependency,
    cipher: CipherDependency,
    workspace_dir: WorkspaceDirDependency,
    path: str = Query(min_length=1),
) -> FileContentOut:
    """A file to show in the editor: text with its hash, or what kind of file it is."""
    async with located_files(session, cipher, workspace_dir, project) as located:
        opened = await open_file(located.files, path)
        return FileContentOut(
            path=opened.entry.path,
            name=name_of(opened.entry.path),
            size=opened.entry.size,
            modified=modified_at(opened.entry.modified),
            kind=opened.kind,  # type: ignore[arg-type]  # a plain string in the service; the schema narrows it
            media_type=opened.media_type,
            hash=opened.hash,
            text=opened.text,
            read_only=opened.read_only,
            note=opened.note,
            location=location_out(located),
        )


@router.get("/projects/{project_id}/files/raw", response_class=Response)
async def read_raw_file(
    project: ProjectDependency,
    session: SessionDependency,
    cipher: CipherDependency,
    workspace_dir: WorkspaceDirDependency,
    path: str = Query(min_length=1),
) -> Response:
    """The file itself: images and PDFs inline, anything else as a download."""
    async with located_files(session, cipher, workspace_dir, project) as located:
        data, media_type, name = await raw_file(located.files, path)
    inline = media_type in INLINE_TYPES
    disposition = "inline" if inline else "attachment"
    return Response(
        data,
        media_type=media_type if inline else "application/octet-stream",
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(name)}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


@router.put("/projects/{project_id}/files/content", response_model=FileSavedOut)
async def save_text_file(
    body: FileSaveIn,
    request: Request,
    project: ProjectDependency,
    session: SessionDependency,
    cipher: CipherDependency,
    workspace_dir: WorkspaceDirDependency,
) -> FileSavedOut:
    """Save the editor's text. Refused (409) when the file changed since it was opened; a checkpoint comes first."""
    store = CheckpointStore(request.app.state.session_factory, request.app.state.data_dir)
    async with located_files(session, cipher, workspace_dir, project) as located:
        saved = await save_file(located, store, project, body.path, body.text, body.base_hash)
        # A conversation's shared document may be this file: its running reply looks again.
        notify_changed(request.app.state.runs, project_id=project.id)
        return FileSavedOut(
            path=saved.entry.path,
            size=saved.entry.size,
            modified=modified_at(saved.entry.modified),
            hash=saved.hash,
            checkpoint_id=saved.checkpoint_id,
        )
