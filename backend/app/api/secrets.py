"""Secrets for placeholders: write-only values the agent uses by name."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field

from app.api.dependencies import EnvironmentDependency, SessionDependency
from app.secrets.service import SecretError, check_name, find_secret, list_secrets, save_secret
from app.storage.models import Environment, Project
from app.vault.store import level_of

router = APIRouter(tags=["secrets"])


class SecretOut(BaseModel):
    name: str
    # Where the secret is defined: global, the environment, or this project; the nearest wins by name.
    level: str
    updated_at: datetime


class SecretIn(BaseModel):
    value: str = Field(min_length=1, max_length=8000)


def check_project(session: SessionDependency, environment: Environment, project_id: int | None) -> None:
    if project_id is None:
        return
    project = session.get(Project, project_id)
    if project is None or project.environment_id != environment.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found in this environment.")


@router.get("/environments/{environment_id}/secrets", response_model=list[SecretOut])
def get_secrets(
    environment: EnvironmentDependency, session: SessionDependency, project_id: Annotated[int | None, Query()] = None
) -> list[SecretOut]:
    """The secrets a run sees at this level: the environment's, and for a project its own on top."""
    check_project(session, environment, project_id)
    return [
        SecretOut(name=secret.name, level=level_of(secret), updated_at=secret.updated_at)
        for secret in list_secrets(session, environment.id, project_id)
    ]


@router.put("/environments/{environment_id}/secrets/{name}", response_model=SecretOut)
def put_secret(
    name: str,
    payload: SecretIn,
    request: Request,
    environment: EnvironmentDependency,
    session: SessionDependency,
    project_id: Annotated[int | None, Query()] = None,
) -> SecretOut:
    check_project(session, environment, project_id)
    try:
        secret = save_secret(session, request.app.state.cipher, environment.id, project_id, name, payload.value)
    except SecretError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    return SecretOut(name=secret.name, level=level_of(secret), updated_at=secret.updated_at)


@router.delete("/environments/{environment_id}/secrets/{name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_secret(
    name: str,
    environment: EnvironmentDependency,
    session: SessionDependency,
    project_id: Annotated[int | None, Query()] = None,
) -> Response:
    check_project(session, environment, project_id)
    try:
        check_name(name)
    except SecretError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    secret = find_secret(session, environment.id, project_id, name)
    if secret is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such secret at this level.")
    session.delete(secret)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
