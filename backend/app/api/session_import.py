"""Import a Claude Code or Codex session as a conversation: an upload, or a file picked on a target."""

from typing import Annotated, Literal

from fastapi import APIRouter, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.conversations import summary_out
from app.api.dependencies import CipherDependency, EnvironmentDependency, ProviderRegistryDependency, SessionDependency
from app.api.schemas import ConversationSummaryOut
from app.imports import service
from app.imports.sessions import ImportedSession, SessionImportError, parse_session
from app.providers.registry import ProviderRegistry
from app.storage.models import Conversation, Environment, Project, Target
from app.targets.base import TargetError

router = APIRouter(tags=["conversations"])


class SessionImportOut(ConversationSummaryOut):
    # What the imported session hides from a reader (invisible, direction or lookalike characters), to look at before
    # continuing the conversation.
    warnings: list[str]


class SessionFileOut(BaseModel):
    # Relative to the home folder on the target.
    path: str
    tool: Literal["claude_code", "codex"]
    size: int
    # Last change as a Unix timestamp, when the target reports it.
    modified: float | None
    # Claude Code's folder for the working folder the session ran in, or Codex's date folder.
    folder: str


class TargetImportIn(BaseModel):
    target_id: int
    path: str = Field(min_length=1, max_length=1000)
    project_id: int | None = None
    model: str
    effort: str
    preset: str | None = None


def check_choice(
    session: Session, providers: ProviderRegistry, environment: Environment, project_id: int | None, model_id: str, effort: str
) -> str:
    """The provider of the model the conversation continues with, after checking the model, effort and project."""
    model = providers.find_model(model_id)
    if model is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown model.")
    if effort not in model.efforts:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Effort must be one of: {', '.join(model.efforts)}.")
    if project_id is not None:
        project = session.get(Project, project_id)
        if project is None or project.environment_id != environment.id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown project in this environment.")
    return model.provider


def parsed(data: bytes) -> ImportedSession:
    try:
        return parse_session(data)
    except SessionImportError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error


def imported_out(conversation: Conversation, imported: ImportedSession) -> SessionImportOut:
    return SessionImportOut(**summary_out(conversation).model_dump(), warnings=service.hidden_warnings(imported))


def find_target(session: Session, environment: Environment, target_id: int) -> Target:
    target = session.get(Target, target_id)
    if target is None or target.environment_id != environment.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Target not found in this environment.")
    return target


@router.post(
    "/environments/{environment_id}/conversations/import",
    response_model=SessionImportOut,
    status_code=status.HTTP_201_CREATED,
)
async def import_session(
    file: UploadFile,
    environment: EnvironmentDependency,
    session: SessionDependency,
    providers: ProviderRegistryDependency,
    model: Annotated[str, Form()],
    effort: Annotated[str, Form()],
    project_id: Annotated[int | None, Form()] = None,
    preset: Annotated[str | None, Form()] = None,
) -> SessionImportOut:
    """An uploaded Claude Code (.jsonl) or Codex (rollout-*.jsonl) session as a new conversation. Its messages and
    tool calls are stored as read-only blocks; nothing in it is run."""
    provider = check_choice(session, providers, environment, project_id, model, effort)
    data = await file.read(service.MAX_IMPORT_BYTES + 1)
    if len(data) > service.MAX_IMPORT_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "The session file is larger than 50 MB.")
    imported = parsed(data)
    conversation = service.create_conversation(
        session, environment.id, project_id, provider, model, effort, clean_preset(preset), imported, "upload", file.filename or "session.jsonl"
    )
    return imported_out(conversation, imported)


@router.get("/environments/{environment_id}/targets/{target_id}/sessions", response_model=list[SessionFileOut])
async def list_target_sessions(
    target_id: int, environment: EnvironmentDependency, session: SessionDependency, cipher: CipherDependency
) -> list[SessionFileOut]:
    """Claude Code and Codex session files in the home folder of the target's user, newest first."""
    target = find_target(session, environment, target_id)
    try:
        files = service.home_files(session, cipher, target)
        session.close()
        found = await service.list_session_files(files)
    except TargetError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    return [
        SessionFileOut(path=item.path, tool=item.tool, size=item.size, modified=item.modified, folder=item.folder)  # type: ignore[arg-type]
        for item in found
    ]


@router.post(
    "/environments/{environment_id}/conversations/import-from-target",
    response_model=SessionImportOut,
    status_code=status.HTTP_201_CREATED,
)
async def import_target_session(
    payload: TargetImportIn,
    environment: EnvironmentDependency,
    session: SessionDependency,
    providers: ProviderRegistryDependency,
    cipher: CipherDependency,
) -> SessionImportOut:
    """A session file read from a target (only from ~/.claude/projects and ~/.codex/sessions) as a new conversation."""
    provider = check_choice(session, providers, environment, payload.project_id, payload.model, payload.effort)
    target = find_target(session, environment, payload.target_id)
    try:
        data = await service.read_session_file(service.home_files(session, cipher, target), payload.path)
    except SessionImportError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    except TargetError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    imported = parsed(data)
    conversation = service.create_conversation(
        session,
        environment.id,
        payload.project_id,
        provider,
        payload.model,
        payload.effort,
        clean_preset(payload.preset),
        imported,
        f"target:{target.name}",
        payload.path.rsplit("/", 1)[-1],
    )
    return imported_out(conversation, imported)


def clean_preset(preset: str | None) -> str | None:
    return " ".join(preset.split()) or None if preset else None
