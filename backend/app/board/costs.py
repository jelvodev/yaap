"""What a card has cost and how that compares with a budget."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.storage.models import AgentRunRecord, UsageRecord

BudgetLevel = Literal["none", "ok", "warn", "over"]
# The share of a budget from which it is shown as nearly used up.
WARN_SHARE = 0.8


def card_cost(session: Session, conversation_ids: Sequence[int]) -> float:
    """Charged usage of the card's conversations; sub-runs record their usage on the same conversation."""
    if not conversation_ids:
        return 0.0
    total = session.scalar(
        select(func.coalesce(func.sum(UsageRecord.cost), 0.0)).where(UsageRecord.conversation_id.in_(conversation_ids))
    )
    return round(float(total or 0.0), 2)


def card_attempts(session: Session, build_run_ids: Sequence[int]) -> int:
    """Step attempts of the card's builds: each attempt is a sub-run of its build run."""
    if not build_run_ids:
        return 0
    count = session.scalar(
        select(func.count()).select_from(AgentRunRecord).where(AgentRunRecord.parent_run_id.in_(build_run_ids))
    )
    return int(count or 0)


def budget_level(spent: float, budget: float | None) -> BudgetLevel:
    if budget is None:
        return "none"
    if budget == 0:
        return "ok" if spent == 0 else "over"
    if spent > budget:
        return "over"
    if spent >= WARN_SHARE * budget:
        return "warn"
    return "ok"


@dataclass(frozen=True)
class VersionSummary:
    done: int
    total: int
    cost_usd: float
    budget_usd: float | None
    level: BudgetLevel


def version_summary(cards: Sequence[tuple[str, float]], budget_usd: float | None) -> VersionSummary:
    """Progress and cost of a version from its cards' `(column, cost_usd)`; broken cards are not passed in."""
    cost = round(sum(cost_usd for _column, cost_usd in cards), 2)
    done = sum(1 for column, _cost in cards if column == "done")
    return VersionSummary(done, len(cards), cost, budget_usd, budget_level(cost, budget_usd))


def conversation_costs(session: Session, conversation_ids: Sequence[int]) -> dict[int, float]:
    """Charged usage per conversation, for many cards' costs in one query."""
    if not conversation_ids:
        return {}
    rows = session.execute(
        select(UsageRecord.conversation_id, func.coalesce(func.sum(UsageRecord.cost), 0.0))
        .where(UsageRecord.conversation_id.in_(conversation_ids))
        .group_by(UsageRecord.conversation_id)
    )
    return {conversation_id: float(total or 0.0) for conversation_id, total in rows if conversation_id is not None}
