"""A project's sandbox: choosing it, its state, and stopping or rebuilding it."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select

from app.api.dependencies import CipherDependency, ProjectDependency, SessionDependency
from app.core.settings import SETTING_DEFINITIONS, resolve_setting, store_setting
from app.sandbox.client import NOT_ENABLED, SandboxUnavailable
from app.sandbox.service import (
    SANDBOX_IDLE,
    SANDBOXES,
    connection_key,
    find_sandbox,
    prepare_sandbox,
    sandbox_key,
)
from app.storage.models import Conversation, Project, Target
from app.targets.base import TargetError
from app.targets.ssh import CONNECTIONS

router = APIRouter(tags=["sandbox"])


class SandboxIn(BaseModel):
    network: bool = False


class SandboxOut(BaseModel):
    # Whether the helper is there and answers (the Compose profile sandbox); detail says why not.
    available: bool
    detail: str | None
    # Whether the project's setting Where the agent works is the sandbox.
    chosen: bool
    network: bool
    # missing (never started or removed), created, running, exited, or what Docker reports.
    state: str
    cpu_percent: float | None = None
    memory_bytes: int | None = None
    memory_limit: int | None = None
    started_at: str | None = None
    last_used_at: datetime | None = None
    # When the idle stop will stop it, while it runs.
    stops_at: datetime | None = None


def chosen_value(session: SessionDependency, project: Project) -> dict | None:
    value = resolve_setting(session, SETTING_DEFINITIONS["target"], project.environment_id, project.id).value
    return value if isinstance(value, dict) and value.get("sandbox") else None


async def sandbox_out(session: SessionDependency, project: Project) -> SandboxOut:
    value = chosen_value(session, project)
    target = find_sandbox(session, project.id)
    network = bool(value.get("network")) if value else bool(target and target.network)
    found = SandboxOut(available=False, detail=NOT_ENABLED, chosen=value is not None, network=network, state="missing")
    if SANDBOXES.helper is None:
        return found
    try:
        if target is None:
            await SANDBOXES.helper.health()
            answer: dict = {"state": "missing"}
        else:
            answer = await SANDBOXES.helper.status(sandbox_key(project.id))
    except SandboxUnavailable as error:
        found.detail = str(error)
        return found
    found.available, found.detail = True, None
    found.state = str(answer.get("state") or "unknown")
    found.cpu_percent = answer.get("cpu_percent")
    found.memory_bytes = answer.get("memory_bytes")
    found.memory_limit = answer.get("memory_limit")
    found.started_at = answer.get("started_at")
    if target is not None:
        found.last_used_at = target.last_used_at
        if found.state == "running" and target.last_used_at is not None:
            found.stops_at = target.last_used_at + SANDBOX_IDLE
    return found


@router.get("/projects/{project_id}/sandbox", response_model=SandboxOut)
async def get_sandbox(project: ProjectDependency, session: SessionDependency) -> SandboxOut:
    """The project's sandbox: whether it can be used, whether it is chosen, and its state, CPU and memory."""
    return await sandbox_out(session, project)


@router.put("/projects/{project_id}/sandbox", response_model=SandboxOut)
async def choose_sandbox(
    payload: SandboxIn, project: ProjectDependency, session: SessionDependency, cipher: CipherDependency
) -> SandboxOut:
    """Let the project's conversations work in its sandbox, with or without network. The container starts on first
    use; a change of the network takes effect from the next connection, when the sandbox is made again."""
    if SANDBOXES.helper is None:
        raise HTTPException(status.HTTP_409_CONFLICT, NOT_ENABLED)
    target = prepare_sandbox(session, cipher, project, payload.network)
    session.commit()
    store_setting(
        session, SETTING_DEFINITIONS["target"], project.environment_id, {"sandbox": True, "network": payload.network}, project.id
    )
    # The next use starts (and if needed remakes) the container with the network as now chosen.
    CONNECTIONS.close(connection_key(target))
    return await sandbox_out(session, project)


def existing_sandbox(session: SessionDependency, project: Project) -> Target:
    target = find_sandbox(session, project.id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This project has no sandbox.")
    return target


@router.post("/projects/{project_id}/sandbox/stop", response_model=SandboxOut)
async def stop_sandbox(project: ProjectDependency, session: SessionDependency) -> SandboxOut:
    """Stop the sandbox now; it starts again on the next command. Running commands and processes in it end."""
    try:
        await SANDBOXES.stop(existing_sandbox(session, project))
    except SandboxUnavailable as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    return await sandbox_out(session, project)


@router.post("/projects/{project_id}/sandbox/rebuild", response_model=SandboxOut)
async def rebuild_sandbox(
    request: Request, project: ProjectDependency, session: SessionDependency, cipher: CipherDependency
) -> SandboxOut:
    """Remove the sandbox and start a new one from the current image: what was installed outside the project folder
    is gone, the project folder stays. Refused while a reply runs in the project."""
    target = existing_sandbox(session, project)
    conversation_ids = session.scalars(select(Conversation.id).where(Conversation.project_id == project.id))
    if any(request.app.state.replies.running(conversation_id) for conversation_id in conversation_ids):
        raise HTTPException(status.HTTP_409_CONFLICT, "A reply is still running in this project. Wait for it or stop it first.")
    try:
        await SANDBOXES.rebuild(session, cipher, target)
    except TargetError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    return await sandbox_out(session, project)
