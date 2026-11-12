"""Continuing a long conversation as a new one that starts from a summary.

The new conversation sits in the same project with the same preset, model and settings, and points back at the
earlier one. It works for every connection: the summary is a `summary` block that `apply_summary` puts in front of
the first user message.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.chat_history import is_part_of_history, load_branch
from app.core.summaries import (
    SUMMARY_TOKENS,
    SummaryError,
    latest_summary,
    summary_block,
    summary_prompt,
)
from app.core.utility import UtilityContext, UtilitySkipped, run_utility_or_skip
from app.storage.models import Conversation, Message

TITLE_LENGTH = 200


@dataclass(frozen=True)
class Continuation:
    conversation_id: int
    covered: int


@dataclass(frozen=True)
class LinkedConversation:
    id: int
    title: str


@dataclass(frozen=True)
class ContinuationLinks:
    continued_from: LinkedConversation | None
    continued_in: list[LinkedConversation]


def continuation_links(session: Session, conversation: Conversation) -> ContinuationLinks:
    """The conversation this one continues and the ones that continue it, oldest first."""
    earlier_id = conversation.continued_from_id
    earlier = session.get(Conversation, earlier_id) if earlier_id is not None else None
    later = session.scalars(
        select(Conversation)
        .where(Conversation.continued_from_id == conversation.id)
        .order_by(Conversation.created_at, Conversation.id)
    )
    return ContinuationLinks(
        continued_from=LinkedConversation(earlier.id, earlier.title) if earlier is not None else None,
        continued_in=[LinkedConversation(item.id, item.title) for item in later],
    )


def new_conversation(old: Conversation) -> Conversation:
    """A conversation with the old one's settings; what belongs to the old one alone (import, task, archive) stays."""
    now = datetime.now(UTC)
    return Conversation(
        environment_id=old.environment_id,
        project_id=old.project_id,
        title=old.title[:TITLE_LENGTH],
        provider=old.provider,
        model=old.model,
        effort=old.effort,
        agent=old.agent,
        pinned_skills=list(old.pinned_skills or []),
        preset=old.preset,
        access=old.access,
        target_id=old.target_id,
        context_limit=old.context_limit,
        continued_from_id=old.id,
        created_at=now,
        updated_at=now,
    )


async def continue_from_summary(context: UtilityContext, conversation_id: int) -> Continuation:
    """Summarise the conversation and start a new one from that summary."""
    with context.session_factory() as session:
        conversation = session.get_one(Conversation, conversation_id)
        branch = load_branch(session, conversation_id, conversation.head_message_id)
        if not any(message.role == "assistant" and is_part_of_history(message) for message in branch):
            raise SummaryError("There is nothing to summarise yet.")
        previous_index = latest_summary(branch)
        marker = summary_block(branch[previous_index]) if previous_index is not None else None
        previous = str(marker["text"]) if marker is not None else None
        start = previous_index + 1 if previous_index is not None else 0
        environment_id = conversation.environment_id
        covered = len(branch)
        prompt = summary_prompt(previous, branch[start:])
    text = await run_utility_or_skip(
        context, environment_id, prompt, conversation_id, max_tokens=SUMMARY_TOKENS, task="summary", role="worker"
    )
    if isinstance(text, UtilitySkipped):
        raise SummaryError(text.message)
    if not text:
        raise SummaryError("No model was available to write the summary. Check the connection and the budget.")
    with context.session_factory() as session:
        created = new_conversation(session.get_one(Conversation, conversation_id))
        session.add(created)
        session.flush()
        # An assistant message without provider_raw is left out of the model input (is_part_of_history), so the
        # summary reaches the model only through apply_summary, in front of the first user message, for every
        # connection alike.
        message = Message(
            conversation_id=created.id,
            parent_id=None,
            role="assistant",
            provider_raw=None,
            status="complete",
            content=[{"type": "summary", "text": text.strip(), "covered": covered}],
        )
        session.add(message)
        session.flush()
        created.head_message_id = message.id
        session.commit()
        return Continuation(created.id, covered)
