"""GitHub per environment: the token check, repositories and templates, and per project its branches
and pull requests.

Branch actions are local git; pull requests go through GitHub's API with the environment's (or the project's)
GitHub token. Creating and merging a pull request are actions the user confirmed in the screen (choice 12).
"""

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import (
    CipherDependency,
    EnvironmentDependency,
    SessionDependency,
    WorkspaceDirDependency,
)
from app.api.git import RepositoryDependency, github_token, status_out, RepositoryOut
from app.core.settings import resolve_languages
from app.core.utility import UtilityContext, propose_pull_request
from app.credentials.crypto import CredentialCipher, CredentialDecryptionError
from app.credentials.store import read_credential_value
from app.git.service import (
    GitError,
    branch_summary,
    create_branch,
    delete_branch,
    fetch,
    list_branches,
    push,
    remote_url,
    repository_status,
    switch_branch,
)
from app.github.client import MERGE_METHODS, GitHubClient, GitHubError, GitHubRepository, PullRequest, repository_name
from app.projects.service import ProjectError, create_cloned_project, project_path
from app.storage.models import Environment, Project

router = APIRouter(tags=["github"])


class TokenCheckOut(BaseModel):
    kind: Literal["classic", "fine_grained", "none"]
    login: str | None = None
    scopes: list[str] = []
    missing: list[str] = []
    message: str


class GitHubRepositoryOut(BaseModel):
    full_name: str
    private: bool
    default_branch: str
    is_template: bool
    html_url: str
    clone_url: str
    description: str
    updated_at: str | None = None
    # The projects of this environment whose remote is this repository.
    projects: list[str] = []


class RepositoryCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._-]+$")
    private: bool = True
    description: str = Field(default="", max_length=350)
    # owner/name of a template repository to create it from (choice 13); empty for an empty repository.
    template: str | None = Field(default=None, max_length=200)
    # An organisation to create it in; empty for the token's own account.
    owner: str | None = Field(default=None, max_length=100)
    # Clone it into a new project with this name.
    project_name: str | None = Field(default=None, max_length=120)


class RepositoryCreatedOut(BaseModel):
    repository: GitHubRepositoryOut
    project_id: int | None = None


class TemplateIn(BaseModel):
    is_template: bool


class BranchOut(BaseModel):
    name: str
    current: bool
    upstream: str | None
    track: str
    subject: str


class BranchesOut(BaseModel):
    branches: list[BranchOut]
    repository: RepositoryOut


class BranchIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    # Create it from this branch or commit; empty for the current one.
    start: str | None = Field(default=None, max_length=200)
    force: bool = False


class PullOut(BaseModel):
    number: int
    title: str
    state: str
    draft: bool
    merged: bool
    author: str
    head: str
    base: str
    html_url: str
    updated_at: str | None = None


class PullFileOut(BaseModel):
    path: str
    status: str
    additions: int
    deletions: int
    patch: str


class CheckOut(BaseModel):
    name: str
    status: str
    conclusion: str | None = None


class PullDetailOut(PullOut):
    body: str
    mergeable: bool | None = None
    files: list[PullFileOut]
    checks: list[CheckOut]


class PullsOut(BaseModel):
    # owner/name on GitHub, or null when the project's remote is not on GitHub.
    repository: str | None
    default_branch: str | None = None
    current_branch: str | None = None
    pulls: list[PullOut]


class PullCreateIn(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    body: str = Field(default="", max_length=60000)
    # The branch to merge into; empty for the repository's default branch.
    base: str | None = Field(default=None, max_length=200)
    draft: bool = False


class PullProposalIn(BaseModel):
    base: str | None = Field(default=None, max_length=200)


class PullProposalOut(BaseModel):
    title: str
    body: str


class MergeIn(BaseModel):
    method: Literal["squash", "merge", "rebase"] = "squash"
    delete_branch: bool = False


class MergeOut(BaseModel):
    sha: str
    branch_deleted: bool


def repository_out(repository: GitHubRepository, projects: list[str] | None = None) -> GitHubRepositoryOut:
    return GitHubRepositoryOut(**repository.__dict__, projects=projects or [])


def pull_out(pull: PullRequest) -> PullOut:
    return PullOut(
        number=pull.number,
        title=pull.title,
        state=pull.state,
        draft=pull.draft,
        merged=pull.merged,
        author=pull.author,
        head=pull.head,
        base=pull.base,
        html_url=pull.html_url,
        updated_at=pull.updated_at,
    )


def environment_token(session: Session, cipher: CredentialCipher, environment: Environment, action: str) -> str | None:
    try:
        return read_credential_value(session, cipher, environment.id, "github_token", used_by=f"GitHub: {action}")
    except CredentialDecryptionError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error


def client_for(request: Request, token: str | None) -> GitHubClient:
    try:
        return GitHubClient(token)
    except GitHubError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error


def github_failure(error: GitHubError | GitError) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, str(error))


def projects_by_repository(session: Session, workspace_dir: Any, environment: Environment) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for project in session.scalars(select(Project).where(Project.environment_id == environment.id)):
        name = repository_name(project.repository_url) or repository_name(remote_url(project_path(workspace_dir, project)))
        if name:
            found.setdefault(name.lower(), []).append(project.name)
    return found


# --- Environment: token and repositories -----------------------------------------------------------------------


@router.get("/environments/{environment_id}/github/token", response_model=TokenCheckOut)
def check_token(
    request: Request, environment: EnvironmentDependency, session: SessionDependency, cipher: CipherDependency
) -> TokenCheckOut:
    """What the environment's GitHub token may do (choice 15)."""
    token = environment_token(session, cipher, environment, "token check")
    if not token:
        return TokenCheckOut(kind="none", message="No GitHub token in this environment.")
    with client_for(request, token) as client:
        found = client.check_token()
    return TokenCheckOut(**found.__dict__)


@router.get("/environments/{environment_id}/github/repositories", response_model=list[GitHubRepositoryOut])
def github_repositories(
    request: Request,
    environment: EnvironmentDependency,
    session: SessionDependency,
    cipher: CipherDependency,
    workspace_dir: WorkspaceDirDependency,
) -> list[GitHubRepositoryOut]:
    """The repositories the token can reach, most recently updated first, with the projects that use them."""
    token = environment_token(session, cipher, environment, "list repositories")
    used = projects_by_repository(session, workspace_dir, environment)
    try:
        with client_for(request, token) as client:
            found = client.repositories()
    except GitHubError as error:
        raise github_failure(error) from error
    return [repository_out(item, used.get(item.full_name.lower(), [])) for item in found]


@router.post(
    "/environments/{environment_id}/github/repositories",
    response_model=RepositoryCreatedOut,
    status_code=status.HTTP_201_CREATED,
)
def create_github_repository(
    request: Request,
    payload: RepositoryCreateIn,
    environment: EnvironmentDependency,
    session: SessionDependency,
    cipher: CipherDependency,
    workspace_dir: WorkspaceDirDependency,
) -> RepositoryCreatedOut:
    """Create a repository, empty or from a template, and optionally clone it into a new project."""
    token = environment_token(session, cipher, environment, f"create repository {payload.name}")
    try:
        with client_for(request, token) as client:
            created = client.create_repository(
                payload.name, payload.private, payload.description, payload.template or None, payload.owner or None
            )
    except GitHubError as error:
        raise github_failure(error) from error
    project_id = None
    if payload.project_name and payload.project_name.strip():
        try:
            project = create_cloned_project(
                session, workspace_dir, environment, payload.project_name.strip(), created.clone_url, token
            )
        except (ProjectError, GitError) as error:
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"{created.full_name} was created, but cloning it failed: {error}"
            ) from error
        project_id = project.id
    return RepositoryCreatedOut(repository=repository_out(created), project_id=project_id)


@router.patch("/environments/{environment_id}/github/repositories/{owner}/{name}", response_model=GitHubRepositoryOut)
def mark_template(
    request: Request,
    owner: str,
    name: str,
    payload: TemplateIn,
    environment: EnvironmentDependency,
    session: SessionDependency,
    cipher: CipherDependency,
) -> GitHubRepositoryOut:
    """Mark a repository as a template that new projects can start from, or unmark it (choice 13)."""
    token = environment_token(session, cipher, environment, f"template {owner}/{name}")
    try:
        with client_for(request, token) as client:
            return repository_out(client.set_template(f"{owner}/{name}", payload.is_template))
    except GitHubError as error:
        raise github_failure(error) from error


# --- A project: branches ---------------------------------------------------------------------------------------


def branches_out(repository: Any) -> BranchesOut:
    try:
        branches = list_branches(repository.path)
        return BranchesOut(
            branches=[BranchOut(**branch.__dict__) for branch in branches],
            repository=status_out(repository, repository_status(repository.path)),
        )
    except GitError as error:
        raise github_failure(error) from error


@router.get("/environments/{environment_id}/git/repositories/{repository_id}/branches", response_model=BranchesOut)
def project_branches(repository: RepositoryDependency) -> BranchesOut:
    return branches_out(repository)


@router.post("/environments/{environment_id}/git/repositories/{repository_id}/branches", response_model=BranchesOut)
def new_branch(payload: BranchIn, repository: RepositoryDependency) -> BranchesOut:
    """Create a branch and switch to it; uncommitted changes come along."""
    try:
        create_branch(repository.path, payload.name, payload.start or None)
    except GitError as error:
        raise github_failure(error) from error
    return branches_out(repository)


@router.post("/environments/{environment_id}/git/repositories/{repository_id}/branches/switch", response_model=BranchesOut)
def change_branch(payload: BranchIn, repository: RepositoryDependency) -> BranchesOut:
    """Switch to a branch; git refuses when uncommitted changes would be overwritten."""
    try:
        switch_branch(repository.path, payload.name)
    except GitError as error:
        raise github_failure(error) from error
    return branches_out(repository)


@router.post("/environments/{environment_id}/git/repositories/{repository_id}/branches/delete", response_model=BranchesOut)
def remove_branch(payload: BranchIn, repository: RepositoryDependency) -> BranchesOut:
    """Delete a local branch other than the current one; unmerged commits need force."""
    try:
        delete_branch(repository.path, payload.name, payload.force)
    except GitError as error:
        raise github_failure(error) from error
    return branches_out(repository)


# --- A project: pull requests -----------------------------------------------------------------------------------


def github_repository(repository: Any) -> str:
    name = repository_name(remote_url(repository.path))
    if name is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This project's remote is not a GitHub repository.")
    return name


@router.get("/environments/{environment_id}/git/repositories/{repository_id}/pulls", response_model=PullsOut)
def project_pulls(
    request: Request,
    repository: RepositoryDependency,
    environment: EnvironmentDependency,
    session: SessionDependency,
    cipher: CipherDependency,
    state: Literal["open", "closed", "all"] = "open",
) -> PullsOut:
    name = repository_name(remote_url(repository.path))
    current = repository_status(repository.path).branch
    if name is None:
        return PullsOut(repository=None, current_branch=current, pulls=[])
    token = github_token(session, cipher, environment, repository, "list pull requests")
    try:
        with client_for(request, token) as client:
            details = client.repository(name)
            pulls = client.pulls(name, state)
    except GitHubError as error:
        raise github_failure(error) from error
    return PullsOut(
        repository=name, default_branch=details.default_branch, current_branch=current, pulls=[pull_out(p) for p in pulls]
    )


@router.get(
    "/environments/{environment_id}/git/repositories/{repository_id}/pulls/{number}", response_model=PullDetailOut
)
def project_pull(
    request: Request,
    number: int,
    repository: RepositoryDependency,
    environment: EnvironmentDependency,
    session: SessionDependency,
    cipher: CipherDependency,
) -> PullDetailOut:
    """A pull request with its description, checks and changed files with their diff."""
    name = github_repository(repository)
    token = github_token(session, cipher, environment, repository, f"pull request #{number}")
    try:
        with client_for(request, token) as client:
            pull = client.pull(name, number)
            files = client.pull_files(name, number)
            checks = client.checks(name, pull.head_sha)
    except GitHubError as error:
        raise github_failure(error) from error
    return PullDetailOut(
        **pull_out(pull).model_dump(),
        body=pull.body,
        mergeable=pull.mergeable,
        files=[PullFileOut(**item) for item in files],
        checks=[CheckOut(**item) for item in checks],
    )


@router.post(
    "/environments/{environment_id}/git/repositories/{repository_id}/pulls/proposal", response_model=PullProposalOut
)
async def propose_pull(
    request: Request,
    payload: PullProposalIn,
    repository: RepositoryDependency,
    environment: EnvironmentDependency,
    session: SessionDependency,
) -> PullProposalOut:
    """A title and description proposed by the utility model from the branch's commits; the user reviews it."""
    try:
        current = (await asyncio.to_thread(repository_status, repository.path)).branch
        base = payload.base or "main"
        commits, files = await asyncio.to_thread(branch_summary, repository.path, base)
    except GitError as error:
        raise github_failure(error) from error
    if not current:
        raise HTTPException(status.HTTP_409_CONFLICT, "Switch to a branch first.")
    project_id = int(repository.id.removeprefix("project-")) if repository.kind == "project" else None
    work_language = resolve_languages(session, environment.id, project_id).work
    session.close()
    state = request.app.state
    context = UtilityContext(state.session_factory, state.cipher, state.credential_types, state.providers)
    proposal = await propose_pull_request(
        context, environment.id, current, base, commits, files, work_language, project_id
    )
    if proposal is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "The utility model is not available. Check the Claude connection in Settings."
        )
    return PullProposalOut(title=proposal[0], body=proposal[1])


def open_pull_request(
    client: GitHubClient, folder: Any, token: str | None, title: str, body: str, base: str | None, draft: bool
) -> PullRequest:
    """Push the current branch when GitHub does not have all of it yet, then open the pull request."""
    name = repository_name(remote_url(folder))
    if name is None:
        raise GitHubError("This project's remote is not a GitHub repository.")
    current = repository_status(folder)
    if not current.branch:
        raise GitHubError("Switch to a branch first.")
    base = base or client.repository(name).default_branch
    if current.branch == base:
        raise GitHubError(f"You are on {base}, the branch to merge into. Create a branch for the change first.")
    if not current.upstream or current.ahead:
        push(folder, token)
    return client.create_pull(name, current.branch, base, title, body, draft)


@router.post(
    "/environments/{environment_id}/git/repositories/{repository_id}/pulls",
    response_model=PullOut,
    status_code=status.HTTP_201_CREATED,
)
def create_pull(
    request: Request,
    payload: PullCreateIn,
    repository: RepositoryDependency,
    environment: EnvironmentDependency,
    session: SessionDependency,
    cipher: CipherDependency,
) -> PullOut:
    """Open a pull request from the current branch, pushing it first when needed (the user confirmed it)."""
    token = github_token(session, cipher, environment, repository, "create pull request")
    try:
        with client_for(request, token) as client:
            pull = open_pull_request(client, repository.path, token, payload.title, payload.body, payload.base, payload.draft)
    except (GitHubError, GitError) as error:
        raise github_failure(error) from error
    return pull_out(pull)


@router.post(
    "/environments/{environment_id}/git/repositories/{repository_id}/pulls/{number}/merge", response_model=MergeOut
)
def merge_pull(
    request: Request,
    number: int,
    payload: MergeIn,
    repository: RepositoryDependency,
    environment: EnvironmentDependency,
    session: SessionDependency,
    cipher: CipherDependency,
) -> MergeOut:
    """Merge a pull request the user confirmed, squash by default, and optionally delete its branch (choice 16)."""
    if payload.method not in MERGE_METHODS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown merge method.")
    name = github_repository(repository)
    token = github_token(session, cipher, environment, repository, f"merge pull request #{number}")
    deleted = False
    try:
        with client_for(request, token) as client:
            pull = client.pull(name, number)
            sha = client.merge_pull(name, number, payload.method)
            if payload.delete_branch and pull.head:
                client.delete_branch(name, pull.head)
                deleted = True
    except GitHubError as error:
        raise github_failure(error) from error
    try:
        fetch(repository.path, token)
    except GitError:
        pass  # The merge happened; the panel's fetch shows it later.
    return MergeOut(sha=sha, branch_deleted=deleted)
