"""Shared FastAPI dependencies."""

from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.credentials.crypto import CredentialCipher
from app.credentials.types import CredentialType
from app.providers.registry import ProviderRegistry
from app.storage.models import Environment, Project


def get_session(request: Request) -> Iterator[Session]:
    session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def get_cipher(request: Request) -> CredentialCipher:
    return request.app.state.cipher


def get_credential_types(request: Request) -> dict[str, CredentialType]:
    return request.app.state.credential_types


def get_provider_registry(request: Request) -> ProviderRegistry:
    return request.app.state.providers


def get_files_dir(request: Request) -> Path:
    return request.app.state.files_dir


def get_workspace_dir(request: Request) -> Path:
    return request.app.state.workspace_dir


def get_blueprint_dir(request: Request) -> Path:
    return request.app.state.blueprint_dir


def get_data_dir(request: Request) -> Path:
    return request.app.state.data_dir


WorkspaceDirDependency = Annotated[Path, Depends(get_workspace_dir)]
BlueprintDirDependency = Annotated[Path, Depends(get_blueprint_dir)]
FilesDirDependency = Annotated[Path, Depends(get_files_dir)]
DataDirDependency = Annotated[Path, Depends(get_data_dir)]
SessionDependency = Annotated[Session, Depends(get_session)]
CipherDependency = Annotated[CredentialCipher, Depends(get_cipher)]
CredentialTypesDependency = Annotated[dict[str, CredentialType], Depends(get_credential_types)]
ProviderRegistryDependency = Annotated[ProviderRegistry, Depends(get_provider_registry)]


def get_environment(environment_id: int, session: SessionDependency) -> Environment:
    environment = session.get(Environment, environment_id)
    if environment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Environment not found.")
    return environment


EnvironmentDependency = Annotated[Environment, Depends(get_environment)]


def get_project(project_id: int, session: SessionDependency) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found.")
    return project


ProjectDependency = Annotated[Project, Depends(get_project)]
