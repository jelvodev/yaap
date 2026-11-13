"""Projects per environment, each with a working folder."""

import re
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.processes import ProcessManager
from app.agent.shells import CONTAINER_PLACE, SESSION_IDLE, ShellSessions
from app.agents.definitions import find_agent
from app.api.dependencies import (
    CipherDependency,
    EnvironmentDependency,
    BlueprintDirDependency,
    ProjectDependency,
    SessionDependency,
    WorkspaceDirDependency,
)
from app.api.schemas import (
    ProjectCreate,
    MemoryEntriesOut,
    MemoryEntryEdit,
    MemoryEntryIn,
    MemoryEntryOut,
    ProjectMemoryIn,
    ProjectMemoryOut,
    ProjectMove,
    ProjectMoveOut,
    ProjectOut,
    ProjectUpdate,
)
from app.credentials.crypto import CredentialDecryptionError
from app.credentials.store import read_credential_value
from app.git.service import GitError
from app.github.client import GitHubClient, GitHubError
from app.blueprint.levels import layers_for
from app.board.switch import board_enabled
from app.presets.service import find_preset_by_name
from app.checkpoints.service import remove_project_checkpoints
from app.knowledge.service import remove_project_knowledge
from app.core.utility import UtilityContext
from app.evals.runs import EvalError, delete_project_runs, has_other_conversations
from app.projects.consolidation import app_context, tidy_if_due
from app.projects.suggestions import accept, open_suggestions, similar_entry, suggest
from app.projects.memory import (
    MAX_ENTRY_CHARS,
    SECTIONS,
    TEMPLATE,
    MemoryChangedError,
    ProjectMemoryError,
    delete_entry,
    edit_entry,
    parse_entries,
    read_memory,
    remember,
    write_memory,
)
from app.projects.service import (
    ProjectError,
    create_cloned_project,
    create_empty_project,
    move_project,
    project_path,
    remove_project,
    undo_folder_move,
)
from app.sandbox.service import remove_project_sandbox, sandbox_of
from app.storage.models import (
    Conversation,
    Environment,
    MemorySuggestion,
    ProcessRecord,
    Project,
    ShellSessionRecord,
    Target,
)

router = APIRouter(tags=["projects"])


def project_to_out(project: Project, session: Session) -> ProjectOut:
    return ProjectOut(
        id=project.id,
        environment_id=project.environment_id,
        name=project.name,
        folder=project.folder,
        repository_url=project.repository_url,
        default_agent=project.default_agent,
        default_preset=project.default_preset,
        created_at=project.created_at,
        board_enabled=board_enabled(session, project.environment_id, project.id).enabled,
    )


@router.get("/environments/{environment_id}/projects", response_model=list[ProjectOut])
def list_projects(environment: EnvironmentDependency, session: SessionDependency) -> list[ProjectOut]:
    projects = session.scalars(
        select(Project).where(Project.environment_id == environment.id).order_by(Project.name)
    )
    return [project_to_out(project, session) for project in projects]


@router.post("/environments/{environment_id}/projects", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreate,
    environment: EnvironmentDependency,
    session: SessionDependency,
    cipher: CipherDependency,
    workspace_dir: WorkspaceDirDependency,
) -> ProjectOut:
    name = payload.name.strip()
    try:
        if payload.template:
            project = project_from_template(session, cipher, workspace_dir, environment, name, payload)
        elif payload.repository_url and payload.repository_url.strip():
            token = read_credential_value(session, cipher, environment.id, "github_token", used_by=f"Git clone: {name}")
            project = create_cloned_project(session, workspace_dir, environment, name, payload.repository_url, token)
        else:
            project = create_empty_project(session, workspace_dir, environment, name)
    except (ProjectError, GitError, CredentialDecryptionError) as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    return project_to_out(project, session)


def project_from_template(
    session: SessionDependency,
    cipher: CipherDependency,
    workspace_dir: Path,
    environment: Environment,
    name: str,
    payload: ProjectCreate,
) -> Project:
    """Create a GitHub repository from a template repository, with its .blueprint layer, and clone it (choice 13)."""
    repository_name = (payload.repository_name or "").strip() or re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-")
    if not repository_name:
        raise ProjectError("Name the new repository.")
    token = read_credential_value(session, cipher, environment.id, "github_token", used_by=f"New project from template: {name}")
    try:
        with GitHubClient(token) as client:
            created = client.create_repository(repository_name, payload.private, template=payload.template)
            client.wait_until_filled(created.full_name)
    except GitHubError as error:
        raise ProjectError(str(error)) from error
    return create_cloned_project(session, workspace_dir, environment, name, created.clone_url, token)


@router.get("/projects/{project_id}", response_model=ProjectOut)
def read_project(project: ProjectDependency, session: SessionDependency) -> ProjectOut:
    return project_to_out(project, session)


@router.patch("/projects/{project_id}", response_model=ProjectOut)
def update_project(
    payload: ProjectUpdate,
    project: ProjectDependency,
    session: SessionDependency,
    blueprint_dir: BlueprintDirDependency,
    workspace_dir: WorkspaceDirDependency,
) -> ProjectOut:
    """Rename the project or change the agent and preset its new conversations start with."""
    if payload.name is not None:
        project.name = payload.name.strip()
    if "default_agent" in payload.model_fields_set:
        name = (payload.default_agent or "").strip() or None
        if name is not None:
            environment = session.get_one(Environment, project.environment_id)
            if find_agent(layers_for(blueprint_dir, workspace_dir, environment, project), name) is None:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"There is no agent named {name}.")
        project.default_agent = name
    if "default_preset" in payload.model_fields_set:
        name = (payload.default_preset or "").strip() or None
        if name is not None:
            preset = find_preset_by_name(session, project.environment_id, name)
            if preset is None:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"There is no preset named {name}.")
            name = preset.name
        project.default_preset = name
    project.updated_at = datetime.now(UTC)
    session.commit()
    return project_to_out(project, session)


@router.get("/projects/{project_id}/memory", response_model=ProjectMemoryOut)
def read_project_memory(project: ProjectDependency, workspace_dir: WorkspaceDirDependency) -> ProjectMemoryOut:
    content = read_memory(project_path(workspace_dir, project))
    return ProjectMemoryOut(content=content or TEMPLATE, exists=bool(content))


@router.put("/projects/{project_id}/memory", response_model=ProjectMemoryOut)
def save_project_memory(
    payload: ProjectMemoryIn, project: ProjectDependency, workspace_dir: WorkspaceDirDependency
) -> ProjectMemoryOut:
    """Save the memory as edited by the user; the change is committed on its own in the project."""
    folder = project_path(workspace_dir, project)
    try:
        write_memory(folder, project.environment_id, payload.content, "Update the project memory")
    except ProjectMemoryError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    return ProjectMemoryOut(content=read_memory(folder), exists=True)


def memory_entries(text: str) -> MemoryEntriesOut:
    entries = parse_entries(text)
    sections = list(SECTIONS) + [
        section for section in dict.fromkeys(entry.section for entry in entries) if section and section not in SECTIONS
    ]
    if any(not entry.section for entry in entries):
        sections.insert(0, "")
    return MemoryEntriesOut(
        sections=sections,
        entries=[MemoryEntryOut(id=entry.id, section=entry.section, text=entry.text) for entry in entries],
    )


def tidy_later(request: Request, background: BackgroundTasks, project_id: int, folder: Path) -> None:
    """After a new entry: a memory tidy-up once 20 new entries came, after the response."""
    if getattr(request.app.state, "memory_tidy", True):
        background.add_task(tidy_if_due, app_context(request.app), project_id, folder)


def memory_error(error: ProjectMemoryError) -> HTTPException:
    code = status.HTTP_409_CONFLICT if isinstance(error, MemoryChangedError) else status.HTTP_422_UNPROCESSABLE_CONTENT
    return HTTPException(code, str(error))


class MemorySuggestionOut(BaseModel):
    id: int
    conversation_id: int | None
    section: str
    text: str
    # The existing entry it would replace, or null for a new entry.
    updates_entry: str | None
    created_at: datetime


class AcceptSuggestionIn(BaseModel):
    # The text and section as the user edited them; empty keeps the suggestion's.
    text: str = Field(default="", max_length=MAX_ENTRY_CHARS)
    section: str | None = None


class SuggestIn(BaseModel):
    # The conversation to read; empty is the project's most recently active one.
    conversation_id: int | None = None


def suggestion_out(item: MemorySuggestion) -> MemorySuggestionOut:
    return MemorySuggestionOut(
        id=item.id,
        conversation_id=item.conversation_id,
        section=item.section,
        text=item.text,
        updates_entry=item.updates_entry,
        created_at=item.created_at,
    )


def find_suggestion(session: Session, project: Project, suggestion_id: int) -> MemorySuggestion:
    item = session.get(MemorySuggestion, suggestion_id)
    if item is None or item.project_id != project.id or item.status != "open":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Suggestion not found or already handled.")
    return item


@router.get("/projects/{project_id}/memory/suggestions", response_model=list[MemorySuggestionOut])
def list_memory_suggestions(project: ProjectDependency, session: SessionDependency) -> list[MemorySuggestionOut]:
    """Entries the utility model proposes, waiting for the user."""
    return [suggestion_out(item) for item in open_suggestions(session, project.id)]


@router.post("/projects/{project_id}/memory/suggestions", response_model=list[MemorySuggestionOut])
async def suggest_memory(
    payload: SuggestIn, project: ProjectDependency, session: SessionDependency, request: Request, workspace_dir: WorkspaceDirDependency
) -> list[MemorySuggestionOut]:
    """Ask the utility model now for entries from a conversation of the project; returns all open suggestions."""
    query = select(Conversation).where(Conversation.project_id == project.id)
    if payload.conversation_id is not None:
        query = query.where(Conversation.id == payload.conversation_id)
    conversation = session.scalars(query.order_by(Conversation.updated_at.desc()).limit(1)).first()
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This project has no conversation to read.")
    project_id, conversation_id, folder = project.id, conversation.id, project_path(workspace_dir, project)
    session.close()
    state = request.app.state
    context = UtilityContext(
        state.session_factory, state.cipher, state.credential_types, state.providers, state.blueprint_dir
    )
    await suggest(context, conversation_id, folder)
    with state.session_factory() as fresh:
        return [suggestion_out(item) for item in open_suggestions(fresh, project_id)]


@router.post("/projects/{project_id}/memory/suggestions/{suggestion_id}/accept", response_model=MemoryEntriesOut)
def accept_memory_suggestion(
    suggestion_id: int,
    payload: AcceptSuggestionIn,
    project: ProjectDependency,
    session: SessionDependency,
    workspace_dir: WorkspaceDirDependency,
    request: Request,
    background: BackgroundTasks,
) -> MemoryEntriesOut:
    """Write the suggestion into the memory, as edited: one commit."""
    item = find_suggestion(session, project, suggestion_id)
    if payload.section is not None and payload.section not in SECTIONS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown section; use one of {', '.join(SECTIONS)}.")
    folder = project_path(workspace_dir, project)
    try:
        accept(folder, project.environment_id, item, payload.section, payload.text.strip() or None)
    except ProjectMemoryError as error:
        raise memory_error(error) from error
    item.status = "accepted"
    session.commit()
    tidy_later(request, background, project.id, folder)
    return memory_entries(read_memory(folder))


@router.delete("/projects/{project_id}/memory/suggestions/{suggestion_id}", status_code=status.HTTP_204_NO_CONTENT)
def dismiss_memory_suggestion(suggestion_id: int, project: ProjectDependency, session: SessionDependency) -> Response:
    find_suggestion(session, project, suggestion_id).status = "dismissed"
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/projects/{project_id}/memory/entries", response_model=MemoryEntriesOut)
def list_memory_entries(project: ProjectDependency, workspace_dir: WorkspaceDirDependency) -> MemoryEntriesOut:
    """The memory as entries per section, for the dashboard."""
    return memory_entries(read_memory(project_path(workspace_dir, project)))


@router.post("/projects/{project_id}/memory/entries", response_model=MemoryEntriesOut)
def add_memory_entry(
    payload: MemoryEntryIn,
    project: ProjectDependency,
    workspace_dir: WorkspaceDirDependency,
    request: Request,
    background: BackgroundTasks,
) -> MemoryEntriesOut:
    folder = project_path(workspace_dir, project)
    near = similar_entry(parse_entries(read_memory(folder)), payload.text)
    try:
        result = memory_entries(remember(folder, project.environment_id, payload.section, payload.text))
        result.similar = near.text if near is not None else None
    except ProjectMemoryError as error:
        raise memory_error(error) from error
    tidy_later(request, background, project.id, folder)
    return result


@router.put("/projects/{project_id}/memory/entries/{entry_id}", response_model=MemoryEntriesOut)
def edit_memory_entry(
    entry_id: str, payload: MemoryEntryEdit, project: ProjectDependency, workspace_dir: WorkspaceDirDependency
) -> MemoryEntriesOut:
    """Change one entry; a commit of only the memory file."""
    try:
        return memory_entries(edit_entry(project_path(workspace_dir, project), project.environment_id, entry_id, payload.text))
    except ProjectMemoryError as error:
        raise memory_error(error) from error


@router.delete("/projects/{project_id}/memory/entries/{entry_id}", response_model=MemoryEntriesOut)
def delete_memory_entry(entry_id: str, project: ProjectDependency, workspace_dir: WorkspaceDirDependency) -> MemoryEntriesOut:
    """Remove one entry; a commit of only the memory file."""
    try:
        return memory_entries(delete_entry(project_path(workspace_dir, project), project.environment_id, entry_id))
    except ProjectMemoryError as error:
        raise memory_error(error) from error


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project: ProjectDependency, session: SessionDependency, request: Request) -> Response:
    """Removes the project from YAAP; its folder stays on disk. Its checkpoints, its sandbox and its eval runs (with
    the conversations of their plays) go."""
    project_id = project.id
    sandbox = sandbox_of(session, project_id)
    state = request.app.state
    try:
        if not has_other_conversations(session, project_id):
            delete_project_runs(session, state.files_dir, state.workspace_dir, project_id)
        remove_project(session, project)
    except (ProjectError, EvalError) as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    remove_project_checkpoints(request.app.state.data_dir, project_id)
    remove_project_knowledge(request.app.state.data_dir, project_id)
    await remove_project_sandbox(session, project_id, sandbox)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class ProcessOut(BaseModel):
    id: int
    command: str
    port: int | None
    status: str
    exit_code: int | None
    ended_reason: str | None
    conversation_id: int | None
    started_at: datetime
    ended_at: datetime | None
    # The newest output: live while it runs, as stored when it ended.
    output_tail: str


def process_out(row: ProcessRecord, manager: ProcessManager) -> ProcessOut:
    live = manager.processes.get(row.id)
    return ProcessOut(
        id=row.id,
        command=row.command,
        port=row.port,
        status=live.status if live is not None else row.status,
        exit_code=live.exit_code if live is not None else row.exit_code,
        ended_reason=live.ended_reason if live is not None else row.ended_reason,
        conversation_id=row.conversation_id,
        started_at=row.started_at,
        ended_at=row.ended_at,
        output_tail=live.tail() if live is not None else (row.output_tail or ""),
    )


@router.get("/projects/{project_id}/processes", response_model=list[ProcessOut])
def list_project_processes(project: ProjectDependency, session: SessionDependency, request: Request) -> list[ProcessOut]:
    """The project's background processes: running ones and the most recently ended."""
    manager: ProcessManager = request.app.state.processes
    return [process_out(row, manager) for row in manager.rows(session, project.id)]


@router.post("/projects/{project_id}/processes/{process_id}/stop", response_model=ProcessOut)
async def stop_project_process(project_id: int, process_id: int, request: Request) -> ProcessOut:
    manager: ProcessManager = request.app.state.processes
    live = manager.processes.get(process_id)
    if live is None or live.project_id != project_id or live.status != "running":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No running process with this id in the project.")
    await manager.stop(live, "stopped from the project page")
    with request.app.state.session_factory() as session:
        row = session.get(ProcessRecord, process_id)
        assert row is not None
        return process_out(row, manager)


class ShellSessionOut(BaseModel):
    id: int
    name: str
    conversation_id: int
    conversation_title: str
    # Where its commands run: "YAAP's container" or the target's name.
    place: str
    created_at: datetime
    last_used_at: datetime
    # When it ends unless it is used before then.
    ends_at: datetime


def place_label(session: Session, place: str) -> str:
    if place == CONTAINER_PLACE:
        return "YAAP's container"
    found = re.fullmatch(r"target-(\d+)", place)
    target = session.get(Target, int(found.group(1))) if found else None
    return target.name if target is not None else "a computer that was removed"


@router.get("/projects/{project_id}/shell-sessions", response_model=list[ShellSessionOut])
def list_shell_sessions(
    project: ProjectDependency, session: SessionDependency, request: Request
) -> list[ShellSessionOut]:
    """The open shell sessions of the project's conversations, most recently used first."""
    shells: ShellSessions = request.app.state.shells
    return [
        ShellSessionOut(
            id=row.id,
            name=row.name,
            conversation_id=conversation.id,
            conversation_title=conversation.title,
            place=place_label(session, row.place),
            created_at=row.created_at,
            last_used_at=row.last_used_at,
            ends_at=row.last_used_at + SESSION_IDLE,
        )
        for row, conversation in shells.for_project(session, project.id)
    ]


@router.delete("/projects/{project_id}/shell-sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def end_shell_session(
    project: ProjectDependency, session_id: int, session: SessionDependency, request: Request
) -> Response:
    """End a shell session: its folder and variables are forgotten; the next command with its name starts it again."""
    shells: ShellSessions = request.app.state.shells
    row = session.get(ShellSessionRecord, session_id)
    conversation = session.get(Conversation, row.conversation_id) if row is not None else None
    if row is None or conversation is None or conversation.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No open shell session with this id in the project.")
    shells.end(session, row)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/projects/{project_id}/move", response_model=ProjectMoveOut)
def move_project_to_environment(
    payload: ProjectMove,
    request: Request,
    project: ProjectDependency,
    session: SessionDependency,
    blueprint_dir: BlueprintDirDependency,
    workspace_dir: WorkspaceDirDependency,
) -> ProjectMoveOut:
    """Move the project with its folder and conversations to another environment.

    From then on it works with that environment's connections, GitHub token, Blueprint and presets. A default agent
    or preset that the new environment does not have is cleared.
    """
    target = session.get(Environment, payload.environment_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There is no such environment.")
    conversation_ids = session.scalars(select(Conversation.id).where(Conversation.project_id == project.id))
    if any(request.app.state.replies.busy(conversation_id) for conversation_id in conversation_ids):
        raise HTTPException(status.HTTP_409_CONFLICT, "A reply is still running in this project. Wait for it or stop it first.")
    try:
        old_folder = move_project(session, workspace_dir, project, target)
    except ProjectError as error:
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    try:
        cleared = []
        if project.default_agent and find_agent(layers_for(blueprint_dir, workspace_dir, target, project), project.default_agent) is None:
            cleared.append(f"agent {project.default_agent}")
            project.default_agent = None
        if project.default_preset and find_preset_by_name(session, target.id, project.default_preset) is None:
            cleared.append(f"preset {project.default_preset}")
            project.default_preset = None
        session.commit()
    except Exception:
        folder = project.folder
        session.rollback()
        undo_folder_move(workspace_dir, folder, old_folder)
        raise
    return ProjectMoveOut(project=project_to_out(project, session), cleared=cleared)
