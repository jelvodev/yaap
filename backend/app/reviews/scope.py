"""What a review looks at: the diff, the objective and who wrote the changes.

Three scopes, all read-only for the project:

- a goal (or a scheduled task's run, which is a goal): from the checkpoint before the goal's first changing tool call
  to the folder as it is when the goal ended. A goal without a checkpoint changed nothing, and one whose diff is
  empty neither; neither is reviewed.
- a conversation: the same, from its first changing tool call.
- the project's uncommitted changes: the git diff of the changes view.

The checkpoints' shadow repository diffs the folder without touching the project's own git. The vendors that wrote the
changes are the providers of the replies' and workers' model calls (the routed model of a reply included), from the
usage records; without any, the conversation's provider.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.checkpoints.service import CheckpointStore
from app.checkpoints.shadow import CheckpointError
from app.core.goals import latest_goal
from app.core.search_index import message_text
from app.git.place import git_place
from app.git.service import GitError, repository_diff, repository_log
from app.projects.service import ProjectError, project_path
from app.storage.models import Checkpoint, Conversation, Goal, Message, Project, UsageRecord

ReviewSource = Literal["goal", "task", "changes", "conversation"]
# Usage records of model calls that write code: a reply's and a delegated worker's.
WRITING_PURPOSES = ("main", "worker")


class NothingToReview(Exception):
    """There are no changes to review; the message says why."""


@dataclass(frozen=True)
class ReviewScope:
    project_id: int
    environment_id: int
    source: ReviewSource
    diff: str
    writer_vendors: tuple[str, ...]
    objective: str | None = None
    conversation_id: int | None = None
    goal_id: int | None = None
    # The conversation's preset, whose utility model the review starts from.
    preset: str | None = None


def folder_of(workspace_dir: Path, project: Project) -> Path:
    try:
        return project_path(workspace_dir, project)
    except ProjectError as error:
        raise NothingToReview(str(error)) from error


def checkpoint_diff(
    session: Session,
    store: CheckpointStore,
    project: Project,
    folder: Path,
    conversation_id: int,
    since: datetime | None,
) -> str:
    """The changes since the conversation's first checkpoint before a changing tool call (from `since` on); empty
    when there is none."""
    query = select(Checkpoint).where(
        Checkpoint.project_id == project.id, Checkpoint.conversation_id == conversation_id, Checkpoint.kind == "action"
    )
    if since is not None:
        query = query.where(Checkpoint.created_at >= since)
    first = session.scalar(query.order_by(Checkpoint.id))
    if first is None:
        return ""
    try:
        repository = store.repository(project.id, folder)
        return repository.diff(first.tree, repository.snapshot_tree())
    except CheckpointError as error:
        raise NothingToReview(f"The changes could not be read: {error}") from error


def writer_vendors(session: Session, conversation_ids: list[int], since: datetime | None) -> tuple[str, ...]:
    """The providers of the model calls that wrote in these conversations (from `since` on)."""
    if not conversation_ids:
        return ()
    query = select(UsageRecord.provider).where(
        UsageRecord.conversation_id.in_(conversation_ids), UsageRecord.purpose.in_(WRITING_PURPOSES)
    )
    if since is not None:
        query = query.where(UsageRecord.created_at >= since)
    found = sorted({provider for provider in session.scalars(query.distinct()) if provider})
    if found:
        return tuple(found)
    providers = session.scalars(select(Conversation.provider).where(Conversation.id.in_(conversation_ids)))
    return tuple(sorted({provider for provider in providers if provider}))


def goal_scope(session: Session, store: CheckpointStore, workspace_dir: Path, goal: Goal) -> ReviewScope | None:
    """The changes of an ended goal; None when it changed nothing."""
    conversation = session.get(Conversation, goal.conversation_id)
    if conversation is None or conversation.project_id is None:
        return None
    project = session.get_one(Project, conversation.project_id)
    try:
        folder = folder_of(workspace_dir, project)
        diff = checkpoint_diff(session, store, project, folder, conversation.id, goal.created_at)
    except NothingToReview:
        return None
    if not diff.strip():
        return None
    return ReviewScope(
        project_id=project.id,
        environment_id=project.environment_id,
        source="task" if conversation.scheduled_task_id is not None else "goal",
        diff=diff,
        writer_vendors=writer_vendors(session, [conversation.id], goal.created_at),
        objective=goal.objective,
        conversation_id=conversation.id,
        goal_id=goal.id,
        preset=conversation.preset,
    )


def conversation_objective(session: Session, conversation: Conversation) -> str | None:
    """The conversation's goal, else its first message."""
    goal = latest_goal(session, conversation.id)
    if goal is not None:
        return goal.objective
    first = session.scalar(
        select(Message).where(Message.conversation_id == conversation.id, Message.role == "user").order_by(Message.id)
    )
    return (message_text(first) or None) if first is not None else None


def conversation_scope(
    session: Session, store: CheckpointStore, workspace_dir: Path, conversation: Conversation
) -> ReviewScope:
    if conversation.project_id is None:
        raise NothingToReview("Only a project conversation changes files to review.")
    project = session.get_one(Project, conversation.project_id)
    diff = checkpoint_diff(session, store, project, folder_of(workspace_dir, project), conversation.id, None)
    if not diff.strip():
        raise NothingToReview("This conversation has changed no files.")
    return ReviewScope(
        project_id=project.id,
        environment_id=project.environment_id,
        source="conversation",
        diff=diff,
        writer_vendors=writer_vendors(session, [conversation.id], None),
        objective=conversation_objective(session, conversation),
        conversation_id=conversation.id,
        preset=conversation.preset,
    )


def changes_scope(session: Session, workspace_dir: Path, project: Project) -> ReviewScope:
    """The project's uncommitted changes, read where the project's git runs (call it from a worker thread). They were
    written by the conversations that changed files in the project since the last commit; when there are none, by a
    person, and any vendor may review."""
    folder = folder_of(workspace_dir, project)
    try:
        place = git_place(project.environment_id, folder)
        diff = repository_diff(place)
        last = repository_log(place, limit=1)
    except GitError as error:
        raise NothingToReview(str(error)) from error
    if not diff.strip():
        raise NothingToReview("There are no uncommitted changes to review.")
    since = datetime.fromisoformat(last[0].date) if last else None
    query = select(Checkpoint.conversation_id).where(
        Checkpoint.project_id == project.id, Checkpoint.kind == "action", Checkpoint.conversation_id.is_not(None)
    )
    if since is not None:
        query = query.where(Checkpoint.created_at >= since)
    conversation_ids = sorted({value for value in session.scalars(query.distinct()) if value is not None})
    return ReviewScope(
        project_id=project.id,
        environment_id=project.environment_id,
        source="changes",
        diff=diff,
        writer_vendors=writer_vendors(session, conversation_ids, since),
    )
