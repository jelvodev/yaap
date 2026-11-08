"""Git per environment: the environment's projects and Blueprint layers as repositories.

Repository ids: "project-<id>", "blueprint-global", "blueprint-environment". Only repositories of the
environment in the path are reachable, and that environment's GitHub token is used for remote actions.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Literal

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.dependencies import (
    CipherDependency,
    EnvironmentDependency,
    BlueprintDirDependency,
    SessionDependency,
    WorkspaceDirDependency,
)
from app.core.settings import resolve_languages, resolve_text
from app.core.utility import UtilityContext, UtilitySkipped, propose_commit_message
from app.credentials.crypto import CredentialDecryptionError
from app.credentials.store import read_credential_value
from app.blueprint.levels import environment_layer, global_layer
from app.checkpoints.service import CheckpointStore
from app.git.service import (
    GitError,
    RepositoryStatus,
    commit_all,
    commit_diff,
    fetch,
    init_repository,
    pull,
    push,
    repository_diff,
    repository_log,
    repository_status,
    set_remote_url,
)
from app.git.discard import discard_changes
from app.projects.service import REPLY_RUNNING, project_path, reply_running_in_project
from app.storage.models import Environment, Project

router = APIRouter(prefix="/environments/{environment_id}/git", tags=["git"])


class FileChangeOut(BaseModel):
    path: str
    state: str
    staged: bool


class RepositoryOut(BaseModel):
    id: str
    kind: Literal["project", "blueprint"]
    label: str
    folder: str
    branch: str | None = None
    upstream: str | None = None
    ahead: int = 0
    behind: int = 0
    has_commits: bool = False
    remote_url: str | None = None
    changes: list[FileChangeOut] = []
    error: str | None = None


class DiffOut(BaseModel):
    diff: str


class CommitOut(BaseModel):
    hash: str
    short_hash: str
    author: str
    date: str
    subject: str


class CommitIn(BaseModel):
    message: str = Field(min_length=1, max_length=5000)


class RemoteIn(BaseModel):
    url: str = Field(min_length=1, max_length=400)


class Repository(BaseModel):
    id: str
    kind: Literal["project", "blueprint"]
    label: str
    folder: str
    path: Path


def environment_repositories(
    session: SessionDependency,
    environment: Environment,
    blueprint_dir: Path,
    workspace_dir: Path,
) -> list[Repository]:
    global_root = global_layer(blueprint_dir).root
    environment_root = environment_layer(blueprint_dir, environment).root
    repositories = [
        Repository(id="blueprint-global", kind="blueprint", label="Blueprint · Global", folder="blueprint/global", path=global_root),
        Repository(
            id="blueprint-environment",
            kind="blueprint",
            label=f"Blueprint · {environment.name}",
            folder=f"blueprint/{environment.slug}",
            path=environment_root,
        ),
    ]
    projects = session.scalars(select(Project).where(Project.environment_id == environment.id).order_by(Project.name))
    repositories += [
        Repository(
            id=f"project-{project.id}",
            kind="project",
            label=project.name,
            folder=f"workspace/{project.folder}",
            path=project_path(workspace_dir, project),
        )
        for project in projects
    ]
    return repositories


def find_repository(
    repository_id: str,
    environment: EnvironmentDependency,
    session: SessionDependency,
    blueprint_dir: BlueprintDirDependency,
    workspace_dir: WorkspaceDirDependency,
) -> Repository:
    for repository in environment_repositories(session, environment, blueprint_dir, workspace_dir):
        if repository.id == repository_id:
            if repository.kind == "blueprint":
                init_repository(repository.path)
            return repository
    raise HTTPException(status.HTTP_404_NOT_FOUND, "Repository not found in this environment.")


RepositoryDependency = Annotated[Repository, Depends(find_repository)]


def github_token(
    session: SessionDependency, cipher: CipherDependency, environment: Environment, repository: Repository, action: str
) -> str | None:
    project_id = int(repository.id.removeprefix("project-")) if repository.kind == "project" else None
    try:
        return read_credential_value(
            session, cipher, environment.id, "github_token", project_id, f"Git {action}: {repository.label}"
        )
    except CredentialDecryptionError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error


def status_out(repository: Repository, repo_status: RepositoryStatus | None = None, error: str | None = None) -> RepositoryOut:
    base = RepositoryOut(id=repository.id, kind=repository.kind, label=repository.label, folder=repository.folder)
    if repo_status is None:
        return base.model_copy(update={"error": error})
    return base.model_copy(
        update={
            "branch": repo_status.branch,
            "upstream": repo_status.upstream,
            "ahead": repo_status.ahead,
            "behind": repo_status.behind,
            "has_commits": repo_status.has_commits,
            "remote_url": repo_status.remote_url,
            "changes": [FileChangeOut(path=c.path, state=c.state, staged=c.staged) for c in repo_status.changes],
        }
    )


def run(repository: Repository, action: Callable[[], object]) -> RepositoryOut:
    try:
        action()
        return status_out(repository, repository_status(repository.path))
    except GitError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error


@router.get("/repositories", response_model=list[RepositoryOut])
def list_repositories(
    environment: EnvironmentDependency,
    session: SessionDependency,
    blueprint_dir: BlueprintDirDependency,
    workspace_dir: WorkspaceDirDependency,
) -> list[RepositoryOut]:
    result = []
    for repository in environment_repositories(session, environment, blueprint_dir, workspace_dir):
        if not (repository.path / ".git").exists():
            if repository.kind == "blueprint":
                init_repository(repository.path)
            else:
                result.append(status_out(repository, error="The project folder is not a git repository."))
                continue
        try:
            result.append(status_out(repository, repository_status(repository.path)))
        except GitError as error:
            result.append(status_out(repository, error=str(error)))
    return result


@router.get("/repositories/{repository_id}", response_model=RepositoryOut)
def read_repository(repository: RepositoryDependency) -> RepositoryOut:
    return run(repository, lambda: None)


@router.get("/repositories/{repository_id}/diff", response_model=DiffOut)
def read_diff(repository: RepositoryDependency) -> DiffOut:
    try:
        return DiffOut(diff=repository_diff(repository.path))
    except GitError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error


@router.get("/repositories/{repository_id}/log", response_model=list[CommitOut])
def read_log(
    repository: RepositoryDependency,
    path: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[CommitOut]:
    """Recent commits, newest first; with a path (relative to the repository), only those that touched it."""
    try:
        entries = repository_log(repository.path, path or None, limit)
    except GitError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    return [CommitOut(**entry.__dict__) for entry in entries]


@router.get("/repositories/{repository_id}/commits/{commit}", response_model=DiffOut)
def read_commit(
    commit: str, repository: RepositoryDependency, path: Annotated[str | None, Query(max_length=500)] = None
) -> DiffOut:
    """What one commit changed, optionally limited to one path."""
    try:
        return DiffOut(diff=commit_diff(repository.path, commit, path or None))
    except GitError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error


@router.post("/repositories/{repository_id}/commit", response_model=RepositoryOut)
def commit(
    payload: CommitIn, repository: RepositoryDependency, environment: EnvironmentDependency, session: SessionDependency
) -> RepositoryOut:
    name = resolve_text(session, "git_author_name", environment.id)
    email = resolve_text(session, "git_author_email", environment.id)
    author = (name, email) if name and email else None
    return run(repository, lambda: commit_all(repository.path, payload.message, author))


class CommitMessageOut(BaseModel):
    message: str


@router.post("/repositories/{repository_id}/commit-message", response_model=CommitMessageOut)
async def suggest_commit_message(
    request: Request, repository: RepositoryDependency, environment: EnvironmentDependency, session: SessionDependency
) -> CommitMessageOut:
    """A commit message proposed by the utility model from the current changes; the user reviews it."""
    try:
        repo_status = await anyio.to_thread.run_sync(repository_status, repository.path)
        diff = await anyio.to_thread.run_sync(repository_diff, repository.path)
    except GitError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    if not repo_status.changes:
        raise HTTPException(status.HTTP_409_CONFLICT, "There are no changes to describe.")
    state = request.app.state
    context = UtilityContext(state.session_factory, state.cipher, state.credential_types, state.providers)
    changes = "\n".join(f"{change.state}: {change.path}" for change in repo_status.changes)
    project_id = int(repository.id.removeprefix("project-")) if repository.kind == "project" else None
    work_language = resolve_languages(session, environment.id, project_id).work
    session.close()
    message = await propose_commit_message(context, environment.id, changes, diff, work_language, project_id)
    if isinstance(message, UtilitySkipped):
        raise HTTPException(status.HTTP_409_CONFLICT, message.message)
    if message is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "The utility model is not available. Check the Claude connection in Settings."
        )
    return CommitMessageOut(message=message)


@router.post("/repositories/{repository_id}/fetch", response_model=RepositoryOut)
def fetch_remote(
    repository: RepositoryDependency, environment: EnvironmentDependency, session: SessionDependency, cipher: CipherDependency
) -> RepositoryOut:
    token = github_token(session, cipher, environment, repository, "fetch")
    return run(repository, lambda: fetch(repository.path, token))


@router.post("/repositories/{repository_id}/push", response_model=RepositoryOut)
def push_remote(
    repository: RepositoryDependency, environment: EnvironmentDependency, session: SessionDependency, cipher: CipherDependency
) -> RepositoryOut:
    token = github_token(session, cipher, environment, repository, "push")
    return run(repository, lambda: push(repository.path, token))


@router.post("/repositories/{repository_id}/pull", response_model=RepositoryOut)
def pull_remote(
    repository: RepositoryDependency, environment: EnvironmentDependency, session: SessionDependency, cipher: CipherDependency
) -> RepositoryOut:
    token = github_token(session, cipher, environment, repository, "pull")
    return run(repository, lambda: pull(repository.path, token))


class DiscardIn(BaseModel):
    # One changed file, relative to the repository as the status lists it; null discards every change.
    path: str | None = Field(default=None, min_length=1, max_length=500)


class DiscardOut(BaseModel):
    repository: RepositoryOut
    # The checkpoint of the folder just before the discard; a roll back to it undoes the discard.
    checkpoint_id: int | None


@router.post("/repositories/{repository_id}/discard", response_model=DiscardOut)
def discard(
    payload: DiscardIn,
    request: Request,
    repository: RepositoryDependency,
    environment: EnvironmentDependency,
    session: SessionDependency,
) -> DiscardOut:
    """Discard uncommitted changes of a project: every change, or one changed file's. Tracked files return to HEAD
    (work tree and index), untracked files are removed, ignored files stay. A checkpoint is taken first, and the
    discard is refused while a reply runs in the project."""
    if repository.kind != "project":
        raise HTTPException(status.HTTP_409_CONFLICT, "Changes can only be discarded in a project repository.")
    project_id = int(repository.id.removeprefix("project-"))
    if reply_running_in_project(session, request.app.state.replies.running_in(environment.id), project_id):
        raise HTTPException(status.HTTP_409_CONFLICT, REPLY_RUNNING)
    store = CheckpointStore(request.app.state.session_factory, request.app.state.data_dir)

    def take(summary: str) -> int | None:
        return store.take(project_id, repository.path, "discard", summary, kind="discard")

    try:
        checkpoint_id = discard_changes(repository.path, payload.path, take)
        repo_status = repository_status(repository.path)
    except GitError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    return DiscardOut(repository=status_out(repository, repo_status), checkpoint_id=checkpoint_id)


@router.put("/repositories/{repository_id}/remote", response_model=RepositoryOut)
def update_remote(payload: RemoteIn, repository: RepositoryDependency) -> RepositoryOut:
    return run(repository, lambda: set_remote_url(repository.path, payload.url))
