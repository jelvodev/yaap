"""Removing a conversation for good.

Usage records stay, without the link to the conversation, so the usage overview and budgets keep counting what was
spent. Background processes the conversation started keep running for their project. Everything else of the
conversation goes: its messages (and with them their search entries), attachments and their files, agent runs with
their steps, queued messages, and its shell sessions (their state folders are removed by the caller, with its
outputs); an eval result of it keeps its verdict without the link. The replays of its runs go with their own
conversations; a replay whose own conversation goes keeps its record without the link. Context snapshots are
shared between calls and go when they are pruned (app/tracing/snapshots.py).
"""

from pathlib import Path

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.attachments.files import remove_folder
from app.attachments.service import attachment_folder
from app.storage.models import (
    AgentRunRecord,
    Attachment,
    Conversation,
    EvalResult,
    Goal,
    Message,
    ProcessRecord,
    QueuedMessage,
    Replay,
    RunStep,
    SharedDocument,
    ShellSessionRecord,
    UsageRecord,
)


def delete_conversation(session: Session, files_dir: Path, conversation: Conversation) -> None:
    conversation_id = conversation.id
    replayed = select(Replay.conversation_id).where(
        Replay.source_conversation_id == conversation_id, Replay.conversation_id.is_not(None)
    )
    for replay_conversation in list(session.scalars(select(Conversation).where(Conversation.id.in_(replayed)))):
        delete_conversation(session, files_dir, replay_conversation)
    session.execute(delete(Replay).where(Replay.source_conversation_id == conversation_id))
    session.execute(
        update(Replay).where(Replay.conversation_id == conversation_id).values(conversation_id=None, agent_run_id=None)
    )
    folders = [
        attachment_folder(files_dir, attachment)
        for attachment in session.scalars(select(Attachment).where(Attachment.conversation_id == conversation_id))
    ]
    session.execute(
        update(UsageRecord)
        .where(UsageRecord.conversation_id == conversation_id)
        .values(conversation_id=None, message_id=None)
    )
    session.execute(update(ProcessRecord).where(ProcessRecord.conversation_id == conversation_id).values(conversation_id=None))
    runs = select(AgentRunRecord.id).where(AgentRunRecord.conversation_id == conversation_id)
    session.execute(delete(RunStep).where(RunStep.run_id.in_(runs)))
    # An eval result keeps its verdict and usage; only its link to the play's conversation and run goes.
    session.execute(
        update(EvalResult)
        .where(EvalResult.conversation_id == conversation_id)
        .values(conversation_id=None, agent_run_id=None)
    )
    for model in (Attachment, AgentRunRecord, QueuedMessage, Goal, ShellSessionRecord, SharedDocument, Message):
        session.execute(delete(model).where(model.conversation_id == conversation_id))
    session.delete(conversation)
    session.commit()
    # Files go after the commit, so a failed commit leaves nothing half removed.
    for folder in folders:
        remove_folder(folder)
