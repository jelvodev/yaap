"""What a second opinion looks at: the changes of one reply, or else its answer.

The changes of a reply are the folder's difference from the checkpoint before the reply's first changing tool call
to the state after its last one. The reply's runs are the agent runs that produced its message and, recursively, the
sub-runs they delegated, so a worker's changes count. A reply that took no checkpoint, or whose difference is empty,
changed nothing; its answer is checked instead.
"""

from pathlib import Path

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.checkpoints.service import CheckpointStore
from app.checkpoints.shadow import CheckpointError
from app.core.search_index import message_text
from app.reviews.scope import WRITING_PURPOSES, NothingToReview, ReviewScope, folder_of, writer_vendors
from app.storage.models import AgentRunRecord, Checkpoint, Conversation, Message, Project, UsageRecord

# How far up the branch to look for the message a reply answers.
QUESTION_SEARCH_DEPTH = 6


def reply_run_ids(session: Session, message_id: int) -> list[int]:
    """The runs that produced the reply and all the sub-runs below them."""
    found = list(session.scalars(select(AgentRunRecord.id).where(AgentRunRecord.message_id == message_id)))
    frontier = found
    while frontier:
        frontier = list(session.scalars(select(AgentRunRecord.id).where(AgentRunRecord.parent_run_id.in_(frontier))))
        frontier = [run_id for run_id in frontier if run_id not in found]
        found.extend(frontier)
    return found


def question_of(session: Session, message: Message) -> Message | None:
    """The user message the reply answers: the nearest user message up its branch."""
    current = message
    for _ in range(QUESTION_SEARCH_DEPTH):
        if current.parent_id is None:
            return None
        parent = session.get(Message, current.parent_id)
        if parent is None:
            return None
        if parent.role == "user":
            return parent
        current = parent
    return None


def reply_writers(
    session: Session, conversation: Conversation, message: Message
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The vendors and models that wrote the reply: its own model calls, and the workers' calls between the question
    and the next message (a worker's usage record is not tied to the reply). Without any, the conversation's own."""
    question = question_of(session, message)
    window = [UsageRecord.message_id == message.id]
    if question is not None:
        timed = and_(
            UsageRecord.message_id.is_(None),
            UsageRecord.purpose == "worker",
            UsageRecord.created_at >= question.created_at,
        )
        later = session.scalar(
            select(Message.created_at)
            .where(Message.conversation_id == conversation.id, Message.id > message.id)
            .order_by(Message.id)
            .limit(1)
        )
        window.append(and_(timed, UsageRecord.created_at <= later) if later is not None else timed)
    rows = session.execute(
        select(UsageRecord.provider, UsageRecord.model)
        .where(
            UsageRecord.conversation_id == conversation.id,
            UsageRecord.purpose.in_(WRITING_PURPOSES),
            or_(*window),
        )
        .distinct()
    )
    pairs = [(provider, model) for provider, model in rows if provider and model]
    if not pairs:
        return writer_vendors(session, [conversation.id], None), (conversation.model,)
    return tuple(sorted({provider for provider, _ in pairs})), tuple(sorted({model for _, model in pairs}))


def reply_diff(session: Session, store: CheckpointStore, project: Project, folder: Path, message: Message) -> str:
    """The reply's changes; empty when it took no checkpoint."""
    run_ids = reply_run_ids(session, message.id)
    if not run_ids:
        return ""
    checkpoints = list(
        session.scalars(
            select(Checkpoint)
            .where(
                Checkpoint.project_id == project.id,
                Checkpoint.kind == "action",
                Checkpoint.run_record_id.in_(run_ids),
            )
            .order_by(Checkpoint.id)
        )
    )
    if not checkpoints:
        return ""
    try:
        after = store.next_tree(session, checkpoints[-1], folder)
        return store.repository(project.id, folder).diff(checkpoints[0].tree, after)
    except CheckpointError as error:
        raise NothingToReview(f"The changes could not be read: {error}") from error


def reply_scope(
    session: Session, store: CheckpointStore, workspace_dir: Path, conversation: Conversation, message: Message
) -> ReviewScope | None:
    """The changes of one reply; None when it changed nothing."""
    if conversation.project_id is None:
        return None
    project = session.get_one(Project, conversation.project_id)
    try:
        diff = reply_diff(session, store, project, folder_of(workspace_dir, project), message)
    except NothingToReview:
        return None
    if not diff.strip():
        return None
    question = question_of(session, message)
    vendors, models = reply_writers(session, conversation, message)
    return ReviewScope(
        project_id=project.id,
        environment_id=project.environment_id,
        source="reply",
        diff=diff,
        writer_vendors=vendors,
        objective=(message_text(question) or None) if question is not None else None,
        conversation_id=conversation.id,
        preset=conversation.preset,
        message_id=message.id,
        writer_models=models,
    )


def answer_scope(session: Session, conversation: Conversation, message: Message) -> ReviewScope:
    """The check of a reply's answer to the question it answers; refused when the reply has no text."""
    if not message_text(message):
        raise NothingToReview("This reply has no text to check.")
    question = question_of(session, message)
    vendors, models = reply_writers(session, conversation, message)
    return ReviewScope(
        project_id=conversation.project_id,
        environment_id=conversation.environment_id,
        source="answer",
        diff="",
        writer_vendors=vendors,
        objective=(message_text(question) or None) if question is not None else None,
        conversation_id=conversation.id,
        preset=conversation.preset,
        message_id=message.id,
        writer_models=models,
    )
