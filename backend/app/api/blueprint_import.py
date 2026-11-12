"""Import another tool's setup (or any Markdown documents) into a Blueprint layer: analyze, review, apply."""

from pathlib import Path
from typing import Annotated, Any, Literal

import anyio
import asyncssh
from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel, Field

from app.agent.file_tools import SKIPPED_FOLDERS
from app.agent.tools import TOOLS_BY_NAME
from app.api.blueprint import LayersDependency, MutationStatusOut, mutation_status
from app.api.dependencies import BlueprintDirDependency, CipherDependency, SessionDependency, WorkspaceDirDependency
from app.blueprint.importer import BlueprintImportError, Proposal, item_path
from app.blueprint.layers import ItemKind, Layer, layer_has_file
from app.blueprint.levels import layers_for
from app.blueprint.setup_apply import ApplyFile, ApplyItem, apply_items
from app.blueprint.setup_import import (
    Origin,
    SetupSource,
    collect_project,
    collect_upload,
    count_project_items,
    propose_setup,
)
from app.blueprint.setup_layouts import Layout, Tool
from app.core.utility import UtilityContext
from app.files.service import project_files
from app.projects.service import ProjectError
from app.storage.models import Project
from app.targets.base import TargetError, TargetFiles

router = APIRouter(prefix="/blueprint", tags=["blueprint"])

Destination = Literal["level", "global"]


class ImportFileOut(BaseModel):
    """A file inside a skill's folder."""

    path: str
    content: str
    # A script is only written when it is selected; it is not selected by default.
    script: bool
    selected: bool


class ImportProposalOut(BaseModel):
    source: str
    kind: ItemKind
    name: str
    summary: str
    rules: list[str]
    body: str
    # The body with each hidden character shown as a mark such as ⟦U+200B ZERO WIDTH SPACE⟧.
    body_marked: str
    warnings: list[str]
    # Front matter that YAAP writes for the item (for example tools and role of an agent).
    extra: dict[str, Any]
    proposed_by_model: bool
    # The path the item gets, and whether the layer it defaults to, and the other layer, already have a file there.
    path: str
    exists: bool
    exists_in_level: bool
    exists_in_global: bool
    # The tool the file comes from, when its path says so.
    tool: Tool | None
    # What was not carried over from the source.
    notes: list[str]
    selected: bool
    # Set when the item cannot be imported (a key-shaped value); the user fixes the source and imports again.
    blocked: str | None
    default_layer: Destination
    files: list[ImportFileOut]


class NotImportedOut(BaseModel):
    path: str
    reason: str


class ImportAnalysisOut(BaseModel):
    proposals: list[ImportProposalOut]
    # Files that were found and named but never read or written (settings, hooks, credentials, binaries).
    not_imported: list[NotImportedOut]
    # Files that are not part of a setup (transcripts, caches); only counted.
    skipped: int


class ImportFileIn(BaseModel):
    path: str = Field(min_length=1, max_length=200)
    content: str = Field(max_length=1_000_000)


class ImportItemIn(BaseModel):
    kind: ItemKind
    name: str = Field(min_length=1, max_length=60)
    summary: str = Field(default="", max_length=1000)
    rules: list[str] = Field(default_factory=list, max_length=20)
    body: str = Field(min_length=1, max_length=200_000)
    extra: dict[str, Any] = Field(default_factory=dict)
    layer: Destination = "level"
    # Only for a skill: the selected files of its folder.
    files: list[ImportFileIn] = Field(default_factory=list, max_length=200)


class ImportApplyIn(BaseModel):
    items: list[ImportItemIn] = Field(min_length=1, max_length=60)


class ImportApplyOut(BaseModel):
    written: list[str]
    commits: list[MutationStatusOut]


class ImportFoundOut(BaseModel):
    count: int


def resolve_global_layer(blueprint_dir: BlueprintDirDependency, workspace_dir: WorkspaceDirDependency) -> Layer:
    return layers_for(blueprint_dir, workspace_dir, None, None)[-1]


GlobalLayerDependency = Annotated[Layer, Depends(resolve_global_layer)]


def resolve_project(
    session: SessionDependency, layers: LayersDependency, project_id: Annotated[int | None, Query()] = None
) -> Project:
    # LayersDependency has checked that the project belongs to the environment.
    project = session.get(Project, project_id) if project_id is not None else None
    if project is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Name a project with project_id.")
    return project


ProjectQuery = Annotated[Project, Depends(resolve_project)]


def utility_context(request: Request) -> UtilityContext:
    state = request.app.state
    return UtilityContext(state.session_factory, state.cipher, state.credential_types, state.providers)


def proposal_out(proposal: Proposal, level: Layer, global_layer: Layer) -> ImportProposalOut:
    path = item_path(proposal.kind, proposal.name)
    # A blocked item holds something that looks like a key: none of its text leaves the server.
    hidden = proposal.blocked is not None
    in_level = layer_has_file(level, path)
    in_global = layer_has_file(global_layer, path)
    return ImportProposalOut(
        source=proposal.source,
        kind=proposal.kind,
        name=proposal.name,
        summary="" if hidden else proposal.summary,
        rules=[] if hidden else proposal.rules,
        body="" if hidden else proposal.body,
        body_marked="" if hidden else proposal.body_marked,
        warnings=proposal.warnings,
        extra={} if hidden else proposal.extra,
        proposed_by_model=proposal.proposed_by_model,
        path=path,
        exists=in_global if proposal.default_layer == "global" else in_level,
        exists_in_level=in_level,
        exists_in_global=in_global,
        tool=proposal.tool,
        notes=proposal.notes,
        selected=proposal.selected,
        blocked=proposal.blocked,
        default_layer=proposal.default_layer,
        files=[
            ImportFileOut(
                path=file.path, content="" if hidden else file.content, script=file.script, selected=file.selected
            )
            for file in proposal.files
        ],
    )


async def analysis_out(
    source: SetupSource,
    origin: Origin,
    request: Request,
    utility_environment_id: int | None,
    level: Layer,
    global_layer: Layer,
) -> ImportAnalysisOut:
    context = utility_context(request) if utility_environment_id else None
    proposals = await propose_setup(source.documents, origin, context, utility_environment_id)
    return ImportAnalysisOut(
        proposals=[proposal_out(proposal, level, global_layer) for proposal in proposals],
        not_imported=[NotImportedOut(path=entry.path, reason=entry.reason) for entry in source.not_imported],
        skipped=source.skipped,
    )


@router.post("/import/analyze", response_model=ImportAnalysisOut)
async def analyze_import(
    request: Request,
    file: UploadFile,
    layers: LayersDependency,
    global_layer: GlobalLayerDependency,
    utility_environment_id: Annotated[int | None, Query()] = None,
    origin: Annotated[Origin, Query()] = "project",
    layout: Annotated[Layout | None, Query()] = None,
) -> ImportAnalysisOut:
    """Proposals for a Markdown file or a zip of a setup; nothing is written yet."""
    try:
        source = await anyio.to_thread.run_sync(
            collect_upload, file.file, file.filename or "upload.md", layout, frozenset(SKIPPED_FOLDERS)
        )
    except BlueprintImportError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    return await analysis_out(source, origin, request, utility_environment_id, layers[-1], global_layer)


def located_project_files(
    session: SessionDependency, cipher: CipherDependency, workspace_dir: WorkspaceDirDependency, project: Project
) -> tuple[TargetFiles, Path]:
    """The project's files and its folder; only a folder in YAAP's container, where links can be told apart."""
    try:
        located = project_files(session, cipher, workspace_dir, project)
    except (TargetError, ProjectError) as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    if located.folder is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Importing a project's setup works for projects in YAAP's container."
        )
    return located.files, located.folder


@router.post("/import/project", response_model=ImportAnalysisOut)
async def analyze_project_setup(
    request: Request,
    session: SessionDependency,
    cipher: CipherDependency,
    workspace_dir: WorkspaceDirDependency,
    layers: LayersDependency,
    global_layer: GlobalLayerDependency,
    project: ProjectQuery,
    utility_environment_id: Annotated[int | None, Query()] = None,
) -> ImportAnalysisOut:
    """Proposals for the setup files in a project's own folder; items default to the project's layer."""
    files, root = located_project_files(session, cipher, workspace_dir, project)
    try:
        source = await collect_project(files, set(SKIPPED_FOLDERS), root)
    except TargetError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    except (OSError, asyncssh.Error) as error:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"The files could not be read: {error}") from error
    return await analysis_out(source, "project", request, utility_environment_id, layers[-1], global_layer)


@router.get("/import/project/found", response_model=ImportFoundOut)
async def found_in_project(
    session: SessionDependency,
    cipher: CipherDependency,
    workspace_dir: WorkspaceDirDependency,
    project: ProjectQuery,
) -> ImportFoundOut:
    """How many setup items the project's folder holds, so the project panel offers the import only when it can."""
    try:
        files, root = located_project_files(session, cipher, workspace_dir, project)
    except HTTPException:
        # Not offered for a project outside YAAP's container, or one whose folder cannot be used.
        return ImportFoundOut(count=0)
    try:
        return ImportFoundOut(count=await count_project_items(files, set(SKIPPED_FOLDERS), root))
    except (TargetError, OSError, asyncssh.Error):
        # The offer is a convenience: a folder that cannot be read simply offers nothing.
        return ImportFoundOut(count=0)


@router.post("/import/apply", response_model=ImportApplyOut)
def apply_import(
    payload: ImportApplyIn, layers: LayersDependency, global_layer: GlobalLayerDependency
) -> ImportApplyOut:
    """Write the reviewed items into their layers; each file, or each skill folder, is committed on its own."""
    items = [
        ApplyItem(
            kind=item.kind,
            name=item.name,
            summary=item.summary,
            rules=item.rules,
            body=item.body,
            extra=item.extra,
            layer=item.layer,
            files=tuple(ApplyFile(file.path, file.content) for file in item.files),
        )
        for item in payload.items
    ]
    try:
        result = apply_items(items, layers[-1], global_layer, frozenset(TOOLS_BY_NAME))
    except BlueprintImportError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    return ImportApplyOut(
        written=result.written, commits=[mutation_status(path, committed) for path, committed in result.commits]
    )
