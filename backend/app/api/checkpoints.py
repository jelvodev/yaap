"""Checkpoints of a project folder: list, the changes of one action, and roll back."""

import asyncio
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import select

from app.api.dependencies import ProjectDependency, SessionDependency, WorkspaceDirDependency
from app.checkpoints.service import CheckpointStore
from app.checkpoints.shadow import CheckpointError
from app.core.replies import ReplyRegistry
from app.projects.service import REPLY_RUNNING, ProjectError, project_path, reply_running_in_project
from app.storage.models import Checkpoint, Conversation

router = APIRouter(tags=["checkpoints"])

MAX_DIFF_CHARS = 400_000


class CheckpointOut(BaseModel):
    id: int
    kind: str
    tool: str
    summary: str
    call_key: str | None
    conversation_id: int | None
    conversation_title: str | None
    created_at: datetime


class CheckpointDiffOut(BaseModel):
    diff: str
    paths: list[str] = []
    truncated: bool = False


class RestoreOut(BaseModel):
    # The checkpoint of the state just before the roll back, to undo it.
    before_id: int


def store(request: Request) -> CheckpointStore:
    return CheckpointStore(request.app.state.session_factory, request.app.state.data_dir)


def find(session: SessionDependency, project_id: int, checkpoint_id: int) -> Checkpoint:
    checkpoint = session.get(Checkpoint, checkpoint_id)
    if checkpoint is None or checkpoint.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Checkpoint not found.")
    return checkpoint


def folder_of(project: ProjectDependency, workspace_dir: WorkspaceDirDependency):  # type: ignore[no-untyped-def]
    try:
        return project_path(workspace_dir, project)
    except ProjectError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error


def diff_out(diff: str, paths: list[str] | None = None) -> CheckpointDiffOut:
    truncated = len(diff) > MAX_DIFF_CHARS
    return CheckpointDiffOut(diff=diff[:MAX_DIFF_CHARS], paths=paths or [], truncated=truncated)


@router.get("/projects/{project_id}/checkpoints", response_model=list[CheckpointOut])
def list_checkpoints(
    project: ProjectDependency,
    session: SessionDependency,
    conversation_id: int | None = None,
    limit: int = Query(default=200, ge=1, le=500),
) -> list[CheckpointOut]:
    query = (
        select(Checkpoint, Conversation.title)
        .outerjoin(Conversation, Conversation.id == Checkpoint.conversation_id)
        .where(Checkpoint.project_id == project.id)
        .order_by(Checkpoint.id.desc())
        .limit(limit)
    )
    if conversation_id is not None:
        query = query.where(Checkpoint.conversation_id == conversation_id)
    return [
        CheckpointOut(
            id=checkpoint.id,
            kind=checkpoint.kind,
            tool=checkpoint.tool,
            summary=checkpoint.summary,
            call_key=checkpoint.call_key,
            conversation_id=checkpoint.conversation_id,
            conversation_title=title,
            created_at=checkpoint.created_at,
        )
        for checkpoint, title in session.execute(query)
    ]


@router.get("/projects/{project_id}/checkpoints/{checkpoint_id}/changes", response_model=CheckpointDiffOut)
def checkpoint_changes(
    checkpoint_id: int,
    request: Request,
    project: ProjectDependency,
    session: SessionDependency,
    workspace_dir: WorkspaceDirDependency,
) -> CheckpointDiffOut:
    """What the action after this checkpoint changed: up to the next checkpoint, or to the folder as it is now."""
    checkpoint = find(session, project.id, checkpoint_id)
    try:
        return diff_out(store(request).action_diff(session, checkpoint, folder_of(project, workspace_dir)))
    except CheckpointError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error


@router.get("/projects/{project_id}/checkpoints/{checkpoint_id}/restore-preview", response_model=CheckpointDiffOut)
def restore_preview(
    checkpoint_id: int,
    request: Request,
    project: ProjectDependency,
    session: SessionDependency,
    workspace_dir: WorkspaceDirDependency,
) -> CheckpointDiffOut:
    """What a roll back to this checkpoint would change in the folder as it is now."""
    checkpoint = find(session, project.id, checkpoint_id)
    try:
        diff, paths = store(request).restore_preview(checkpoint, folder_of(project, workspace_dir))
    except CheckpointError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    return diff_out(diff, paths)


@router.post("/projects/{project_id}/checkpoints/{checkpoint_id}/restore", response_model=RestoreOut)
async def restore_checkpoint(
    checkpoint_id: int,
    request: Request,
    project: ProjectDependency,
    session: SessionDependency,
    workspace_dir: WorkspaceDirDependency,
    conversation_id: int | None = None,
) -> RestoreOut:
    """Roll the project folder back to the checkpoint. The state before is kept as a checkpoint of its own."""
    checkpoint = find(session, project.id, checkpoint_id)
    replies: ReplyRegistry = request.app.state.replies
    if reply_running_in_project(session, replies.running_in(project.environment_id), project.id):
        raise HTTPException(status.HTTP_409_CONFLICT, REPLY_RUNNING)
    folder = folder_of(project, workspace_dir)
    try:
        before = await asyncio.to_thread(store(request).restore, checkpoint, folder, conversation_id)
    except CheckpointError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    assert before is not None
    return RestoreOut(before_id=before)
