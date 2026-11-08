"""Projects: a working folder under /workspace/<environment>/<project>, with its conversations."""

import shutil
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.environments.service import slugify
from app.git.service import clone_repository, init_repository, validate_remote_url
from app.storage.models import AgentRunRecord, Attachment, Conversation, Environment, Message, Project, Setting


class ProjectError(Exception):
    """A project action that cannot be done; the message is safe to show to the user."""


def unique_project_slug(session: Session, environment_id: int, name: str) -> str:
    base = slugify(name, "project")
    slug, counter = base, 2
    while session.scalar(
        select(Project.id).where(Project.environment_id == environment_id, Project.slug == slug)
    ) is not None:
        slug, counter = f"{base}-{counter}", counter + 1
    return slug


def project_path(workspace_dir: Path, project: Project) -> Path:
    path = (workspace_dir / project.folder).resolve()
    if not path.is_relative_to(workspace_dir.resolve()):
        raise ProjectError("The project folder lies outside the workspace.")
    return path


REPLY_RUNNING = "A reply is running in this project. Stop it or wait until it ends."


def reply_running_in_project(session: Session, running_conversation_ids: Iterable[int], project_id: int) -> bool:
    """Whether one of the running replies (their conversation ids) belongs to the project: actions that rewrite the
    project folder (a roll back, a discard) wait for it."""
    running = set(running_conversation_ids)
    if not running:
        return False
    found = session.scalars(
        select(Conversation.id).where(Conversation.project_id == project_id, Conversation.id.in_(running))
    ).first()
    return found is not None


def new_project(session: Session, environment: Environment, name: str, repository_url: str | None = None) -> Project:
    slug = unique_project_slug(session, environment.id, name)
    now = datetime.now(UTC)
    project = Project(
        environment_id=environment.id,
        name=name,
        slug=slug,
        folder=f"{environment.slug}/{slug}",
        repository_url=repository_url,
        created_at=now,
        updated_at=now,
    )
    session.add(project)
    session.flush()
    return project


def create_empty_project(session: Session, workspace_dir: Path, environment: Environment, name: str) -> Project:
    """A new project with an empty folder that is a git repository."""
    project = new_project(session, environment, name)
    folder = project_path(workspace_dir, project)
    if folder.exists() and any(folder.iterdir()):
        raise ProjectError(f"The folder {project.folder} already exists and is not empty.")
    init_repository(folder)
    session.commit()
    return project


def create_cloned_project(
    session: Session,
    workspace_dir: Path,
    environment: Environment,
    name: str,
    repository_url: str,
    github_token: str | None,
) -> Project:
    """A new project whose folder is a clone of a remote repository."""
    url = validate_remote_url(repository_url)
    project = new_project(session, environment, name, url)
    folder = project_path(workspace_dir, project)
    if folder.exists() and any(folder.iterdir()):
        raise ProjectError(f"The folder {project.folder} already exists and is not empty.")
    if folder.exists():
        folder.rmdir()
    clone_repository(url, folder, github_token)
    session.commit()
    return project


def remove_project(session: Session, project: Project) -> None:
    """Remove the project from YAAP. The folder on disk is kept."""
    conversations = session.scalar(select(func.count()).select_from(Conversation).where(Conversation.project_id == project.id))
    if conversations:
        raise ProjectError("This project still has conversations. Delete or move them first.")
    session.delete(project)
    session.commit()


# Claude Code keeps its sessions per working folder; this block type marks a reply's session (claude_code_provider).
SESSION_BLOCK_TYPE = "claude_code_session"


def move_project(session: Session, workspace_dir: Path, project: Project, target: Environment) -> Path:
    """Move a project, its folder and its conversations to another environment; returns the old folder.

    Usage records stay with the environment whose connection paid for them. Claude Code sessions are tied to the old
    folder, so the next reply in a moved conversation passes the earlier messages along as a transcript instead.
    The caller commits; on failure it rolls back and calls undo_folder_move.
    """
    if target.id == project.environment_id:
        raise ProjectError("The project is already in that environment.")
    old_folder = project_path(workspace_dir, project)
    # Pick the slug before the project itself counts as part of the target environment.
    slug = unique_project_slug(session, target.id, project.slug)
    project.environment_id = target.id
    project.slug = slug
    project.folder = f"{target.slug}/{project.slug}"
    project.updated_at = datetime.now(UTC)
    new_folder = project_path(workspace_dir, project)
    if new_folder.exists() and any(new_folder.iterdir()):
        raise ProjectError(f"The folder {project.folder} already exists and is not empty.")

    conversation_ids = list(session.scalars(select(Conversation.id).where(Conversation.project_id == project.id)))
    if conversation_ids:
        session.execute(update(Conversation).where(Conversation.id.in_(conversation_ids)).values(environment_id=target.id))
        session.execute(update(Attachment).where(Attachment.conversation_id.in_(conversation_ids)).values(environment_id=target.id))
        for message in session.scalars(select(Message).where(Message.conversation_id.in_(conversation_ids))):
            raw = message.provider_raw or []
            kept = [block for block in raw if block.get("type") != SESSION_BLOCK_TYPE]
            if len(kept) != len(raw):
                message.provider_raw = kept
    session.execute(update(AgentRunRecord).where(AgentRunRecord.project_id == project.id).values(environment_id=target.id))
    session.execute(update(Setting).where(Setting.project_id == project.id).values(environment_id=target.id))

    if old_folder.exists():
        new_folder.parent.mkdir(parents=True, exist_ok=True)
        if new_folder.exists():
            new_folder.rmdir()
        shutil.move(str(old_folder), str(new_folder))
    else:
        new_folder.mkdir(parents=True, exist_ok=True)
    return old_folder


def undo_folder_move(workspace_dir: Path, folder: str, old_folder: Path) -> None:
    """Put the folder back after a move that could not be saved."""
    new_folder = (workspace_dir / folder).resolve()
    if new_folder.exists() and not old_folder.exists():
        shutil.move(str(new_folder), str(old_folder))
