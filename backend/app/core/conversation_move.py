"""Moving a conversation to another project of its environment, or out of its project.

What happens to each table that points at the conversation:

- Moves with it (same transaction): `Conversation.project_id`, its agent runs, the search chunks of its messages
  (they carry the project, so a search in the new project finds them), and, when the target is a project, its
  reviews, memory suggestions and learning proposals. Those three require a project, so with *No project* as the
  target they stay with the old project.
- Comes along without a change: messages, attachments, goals, the shared document and shell sessions (all hang on
  the conversation only). Queued messages do not exist: a move is refused while one waits.
- Stays with the old project: checkpoints (they restore that project's folder), usage records (history that keeps
  budgets right), process records (history of what ran there), eval results and replays.
- Stopped first: the conversation's running processes and its shell sessions.

The conversation gets a note message so the next reply knows the folder changed.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.agent.processes import ProcessManager
from app.agent.runs import RunRegistry
from app.agent.shells import ShellSessions
from app.core.goals import open_goal
from app.core.replies import ReplyRegistry
from app.storage.models import (
    AgentRunRecord,
    Conversation,
    LearningProposal,
    MemorySuggestion,
    Message,
    Project,
    QueuedMessage,
    Review,
    SearchChunk,
    ShellSessionRecord,
)

NO_PROJECT = "No project"


class MoveError(Exception):
    """The conversation cannot be moved; the message is safe to show as it is."""


class MoveBusy(MoveError):
    """A reply, a goal or a queued message of the conversation runs or waits."""


class MoveInvalid(MoveError):
    """The target is not a place this conversation can move to."""


@dataclass(frozen=True)
class MoveResult:
    from_project_id: int | None
    to_project_id: int | None
    stopped_processes: int
    ended_shells: int


def project_label(session: Session, project_id: int | None) -> str:
    project = session.get(Project, project_id) if project_id is not None else None
    return project.name if project is not None else NO_PROJECT


def check_idle(session: Session, replies: ReplyRegistry, runs: RunRegistry, conversation: Conversation) -> None:
    if replies.running(conversation.id) is not None or runs.for_conversation(conversation.id) is not None:
        raise MoveBusy("YAAP is replying in this conversation; stop it first.")
    if open_goal(session, conversation.id) is not None:
        raise MoveBusy("A goal is open in this conversation; end it first.")
    if session.scalar(select(QueuedMessage.id).where(QueuedMessage.conversation_id == conversation.id).limit(1)):
        raise MoveBusy("A message is waiting in the queue of this conversation.")


def check_target(conversation: Conversation, target: Project | None) -> None:
    if target is not None and target.environment_id != conversation.environment_id:
        raise MoveInvalid("A conversation can only move to a project of its own environment.")
    if (target.id if target is not None else None) == conversation.project_id:
        raise MoveInvalid("The conversation is already there.")


async def stop_work(
    session: Session, processes: ProcessManager, shells: ShellSessions, conversation: Conversation
) -> tuple[int, int]:
    running = [
        managed
        for managed in list(processes.processes.values())
        if managed.conversation_id == conversation.id and managed.status == "running"
    ]
    for managed in running:
        await processes.stop(managed, "the conversation moved to another project")
    rows = list(session.scalars(select(ShellSessionRecord).where(ShellSessionRecord.conversation_id == conversation.id)))
    for row in rows:
        shells.end(session, row)
    return len(running), len(rows)


def add_note(session: Session, conversation: Conversation, text: str) -> None:
    """A note in the conversation: shown as a marker and sent to the model as a bracketed line."""
    if conversation.head_message_id is None:
        return
    note = Message(
        conversation_id=conversation.id,
        parent_id=conversation.head_message_id,
        role="user",
        content=[{"type": "note", "text": text}],
        status="complete",
        created_at=datetime.now(UTC),
    )
    session.add(note)
    session.flush()
    conversation.head_message_id = note.id


async def move_conversation(
    session: Session,
    replies: ReplyRegistry,
    runs: RunRegistry,
    processes: ProcessManager,
    shells: ShellSessions,
    conversation: Conversation,
    target: Project | None,
) -> MoveResult:
    check_idle(session, replies, runs, conversation)
    check_target(conversation, target)
    stopped, ended = await stop_work(session, processes, shells, conversation)
    old_id, new_id = conversation.project_id, target.id if target is not None else None
    text = f"Moved from {project_label(session, old_id)} to {project_label(session, new_id)}."
    conversation_id = conversation.id
    conversation.project_id = new_id
    session.execute(
        update(AgentRunRecord).where(AgentRunRecord.conversation_id == conversation_id).values(project_id=new_id)
    )
    session.execute(
        update(SearchChunk)
        .where(SearchChunk.conversation_id == conversation_id, SearchChunk.kind == "message")
        .values(project_id=new_id)
    )
    if new_id is not None:
        for model in (Review, MemorySuggestion, LearningProposal):
            session.execute(update(model).where(model.conversation_id == conversation_id).values(project_id=new_id))
    add_note(session, conversation, text)
    session.commit()
    return MoveResult(old_id, new_id, stopped, ended)
