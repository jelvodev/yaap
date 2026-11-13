"""Maps stored conversations, targets, goals and context control to their API schemas."""

from sqlalchemy.orm import Session

from app.api.schemas import (
    ContextControlOut,
    ConversationSummaryOut,
    ConversationTargetOut,
    GoalOut,
    ImportedFromOut,
)
from app.core.context_control import ContextControl
from app.core.goals import goal_cost
from app.storage.models import Conversation, Goal
from app.targets.base import TargetError
from app.targets.service import chosen_target


def summary_out(conversation: Conversation) -> ConversationSummaryOut:
    return ConversationSummaryOut(
        id=conversation.id,
        project_id=conversation.project_id,
        title=conversation.title,
        provider=conversation.provider,
        model=conversation.model,
        effort=conversation.effort,
        agent=conversation.agent,
        pinned_skills=conversation.pinned_skills or [],
        updated_at=conversation.updated_at,
        archived_at=conversation.archived_at,
        scheduled=conversation.scheduled_task_id is not None,
        unread=bool(conversation.unread),
        access=conversation.access or "default",  # type: ignore[arg-type]  # a plain string column; the schema narrows it
        target_id=conversation.target_id,
        context_limit=conversation.context_limit,
        imported=ImportedFromOut(**conversation.imported_from) if conversation.imported_from else None,
    )


def context_control_out(control: ContextControl) -> ContextControlOut:
    return ContextControlOut(
        limit=control.limit,
        source=control.source,  # type: ignore[arg-type]  # a plain string in the service; the schema narrows it
        auto_compact=control.auto_compact,
        compact_at=control.compact_at,
        trim_at=control.trim_at,
        compacts_itself=control.compacts_itself,
    )


def target_of(session: Session, conversation: Conversation) -> ConversationTargetOut | None:
    try:
        chosen = chosen_target(session, conversation)
    except TargetError:
        return None
    if chosen is None:
        return None
    target, folder = chosen
    return ConversationTargetOut(
        id=target.id,
        name=target.name,
        system=target.system,
        rights=target.rights,
        folder=folder,
        allow_full_access=target.allow_full_access,
    )


def goal_out(session: Session, goal: Goal) -> GoalOut:
    return GoalOut(
        id=goal.id,
        conversation_id=goal.conversation_id,
        objective=goal.objective,
        status=goal.status,
        max_turns=goal.max_turns,
        max_cost=goal.max_cost,
        turns=goal.turns,
        cost=round(goal_cost(session, goal), 4),
        summary=goal.summary,
        created_at=goal.created_at,
        finished_at=goal.finished_at,
    )
