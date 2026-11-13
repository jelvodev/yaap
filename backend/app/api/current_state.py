"""The project's Current state: the short block at the top of its memory that every new conversation starts from."""

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.dependencies import ProjectDependency, WorkspaceDirDependency
from app.projects.current_state import MAX_STATE_CHARS, has_repository, read_state
from app.projects.memory import ProjectMemoryError, read_memory
from app.projects.service import project_path
from app.projects.state_refresh import StateError, save_state, state_updated_at
from app.storage.models import Project

router = APIRouter(tags=["projects"])


class CurrentStateOut(BaseModel):
    # The block's text without its heading; empty when the memory has none yet.
    text: str
    # When it was last written through YAAP (the agent, the user here, or a refresh); null when not known.
    updated_at: datetime | None
    max_chars: int
    # Without a git repository the block cannot be written.
    has_repository: bool


class CurrentStateIn(BaseModel):
    # Checked by the service (empty, over max_chars, a heading line); the field limit only bounds the request.
    text: str = Field(max_length=MAX_STATE_CHARS * 4)


def state_out(project: Project, workspace_dir: Path) -> CurrentStateOut:
    folder = project_path(workspace_dir, project)
    return CurrentStateOut(
        text=read_state(read_memory(folder)) or "",
        updated_at=state_updated_at(folder, project.environment_id),
        max_chars=MAX_STATE_CHARS,
        has_repository=has_repository(folder),
    )


@router.get("/projects/{project_id}/current-state", response_model=CurrentStateOut)
def read_current_state(project: ProjectDependency, workspace_dir: WorkspaceDirDependency) -> CurrentStateOut:
    return state_out(project, workspace_dir)


@router.put(
    "/projects/{project_id}/current-state",
    response_model=CurrentStateOut,
    responses={409: {"description": "no_repository"}, 422: {"description": "state_invalid"}},
)
def save_current_state(
    payload: CurrentStateIn, project: ProjectDependency, workspace_dir: WorkspaceDirDependency
) -> CurrentStateOut:
    """Replace the block as edited by the user; the change is committed on its own in the project."""
    folder = project_path(workspace_dir, project)
    try:
        save_state(folder, project.environment_id, payload.text, "edited by you")
    except StateError as error:
        code = status.HTTP_409_CONFLICT if error.kind == "no_repository" else status.HTTP_422_UNPROCESSABLE_CONTENT
        raise HTTPException(code, str(error)) from error
    except ProjectMemoryError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    return state_out(project, workspace_dir)
