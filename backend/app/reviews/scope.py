"""What a review looks at: the diff, the objective and who wrote the changes.

Three scopes, all read-only for the project:

- a goal (or a scheduled task's run, which is a goal): from the checkpoint before the goal's first changing tool call
  to the folder as it is when the goal ended. A goal without a checkpoint changed nothing, and one whose diff is
  empty neither; neither is reviewed.
- a conversation: the same, from its first changing tool call.
- the project's uncommitted changes: the git diff of the changes view.
- a build: the commits of a build branch since the commit it started from (`base_commit..branch`), in the project
  repository where the build delivered its branch.

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
from app.git.branches import check_branch_name
from app.git.service import (
    COMMIT_HASH,
    MAX_DIFF_CHARS,
    PLAIN_DIFF,
    QUOTEPATH_OFF,
    GitError,
    Repository,
    repository_diff,
    repository_log,
    run_git,
)
from app.projects.service import ProjectError, project_path
from app.storage.models import Checkpoint, Conversation, Goal, Message, Project, UsageRecord

ReviewSource = Literal["goal", "task", "changes", "conversation", "reply", "answer", "build"]
# Usage records of model calls that write code: a reply's and a delegated worker's.
WRITING_PURPOSES = ("main", "worker")


class NothingToReview(Exception):
    """There are no changes to review; the message says why."""


@dataclass(frozen=True)
class ReviewScope:
    # None for the check of an answer in a conversation without a project.
    project_id: int | None
    environment_id: int
    source: ReviewSource
    diff: str
    writer_vendors: tuple[str, ...]
    objective: str | None = None
    conversation_id: int | None = None
    goal_id: int | None = None
    # The conversation's preset, whose utility model the review starts from.
    preset: str | None = None
    # A second opinion: the reply it is about and the models that wrote it.
    message_id: int | None = None
    writer_models: tuple[str, ...] = ()
    # A build's review: what it compares and the card it belongs to.
    base_commit: str | None = None
    branch: str | None = None
    card_id: int | None = None


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


@dataclass(frozen=True)
class BuildTarget:
    """What a build's review compares: the commit the build started from and the branch it delivered."""

    base_commit: str
    branch: str
    objective: str | None = None
    # The conversation the build ran in, whose usage says who wrote the changes.
    conversation_id: int | None = None
    card_id: int | None = None


def branch_diff(place: Repository, base_commit: str, branch: str) -> str:
    """The diff base_commit..branch, read where the project's git runs. The commit must be a hash and the branch a
    valid branch name, so neither can be read as an option. Blocking."""
    if not COMMIT_HASH.fullmatch(base_commit):
        raise GitError("That is not a commit hash.")
    name = check_branch_name(place, branch)
    arguments = ["diff", *PLAIN_DIFF, base_commit, f"refs/heads/{name}", "--"]
    diff = run_git(arguments, place, config=QUOTEPATH_OFF).stdout
    if len(diff) > MAX_DIFF_CHARS:
        return diff[:MAX_DIFF_CHARS] + "\n\n[The diff is longer than this; the rest is not shown.]"
    return diff


def branch_scope(session: Session, workspace_dir: Path, project: Project, target: BuildTarget) -> ReviewScope | None:
    """The changes of a build branch since its base commit; None when the branch changed nothing. Raises
    NothingToReview when the branch cannot be read. Call it from a worker thread."""
    folder = folder_of(workspace_dir, project)
    try:
        diff = branch_diff(git_place(project.environment_id, folder), target.base_commit, target.branch)
    except GitError as error:
        raise NothingToReview(str(error)) from error
    if not diff.strip():
        return None
    conversation_ids = [target.conversation_id] if target.conversation_id is not None else []
    conversation = session.get(Conversation, target.conversation_id) if target.conversation_id is not None else None
    return ReviewScope(
        project_id=project.id,
        environment_id=project.environment_id,
        source="build",
        diff=diff,
        writer_vendors=writer_vendors(session, conversation_ids, None),
        objective=target.objective,
        conversation_id=target.conversation_id,
        preset=conversation.preset if conversation is not None else None,
        base_commit=target.base_commit,
        branch=target.branch,
        card_id=target.card_id,
    )
