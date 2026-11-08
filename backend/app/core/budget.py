"""Monthly budgets for metered use.

A global budget limits the total across all environments; an environment budget limits that environment alone.
When both are set, whichever is reached first applies. Only what is charged per call counts: replies through a
subscription cost nothing per call and are never stopped by a budget.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.settings import resolve_text
from app.storage.models import Environment, UsageRecord

WARN_SHARE = 0.8


@dataclass(frozen=True)
class BudgetLine:
    scope: str  # "environment" or "total"
    label: str
    limit: float
    spent: float

    @property
    def reached(self) -> bool:
        return self.spent >= self.limit


def parse_amount(value: str) -> float | None:
    try:
        amount = float(value.replace(",", ".").replace("$", "").strip())
    except ValueError:
        return None
    return amount if amount >= 0 else None


def month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def spent(session: Session, environment_id: int | None) -> float:
    query = select(func.coalesce(func.sum(UsageRecord.cost), 0.0)).where(UsageRecord.created_at >= month_start())
    if environment_id is not None:
        query = query.where(UsageRecord.environment_id == environment_id)
    return float(session.scalar(query) or 0.0)


def budget_lines(session: Session, environment_id: int) -> list[BudgetLine]:
    """The budgets that apply to an environment, with what was spent this month."""
    lines: list[BudgetLine] = []
    own = parse_amount(resolve_text(session, "budget_environment", environment_id))
    if own is not None:
        environment = session.get(Environment, environment_id)
        name = environment.name if environment else "this environment"
        lines.append(BudgetLine("environment", f"{name} budget", own, spent(session, environment_id)))
    total = parse_amount(resolve_text(session, "budget_total", None))
    if total is not None:
        lines.append(BudgetLine("total", "Budget for all environments", total, spent(session, None)))
    return lines


def budget_refusal(session: Session, environment_id: int) -> str | None:
    """Why metered use must wait until next month, or None while the budgets allow it."""
    for line in budget_lines(session, environment_id):
        if line.reached:
            return (
                f"The {line.label.lower()} of ${line.limit:.2f} for this month is used up (${line.spent:.2f}). "
                "Raise it in Settings, or use the Claude subscription connection."
            )
    return None


def crossed_line(lines: list[BudgetLine], used: float) -> BudgetLine | None:
    """The first budget that a running reply's usage so far, on top of what the month had spent when it started,
    has reached; None while every budget allows more."""
    for line in lines:
        if line.spent + used >= line.limit:
            return line
    return None


def budget_stop_message(line: BudgetLine, used: float) -> str:
    """Why a running reply was stopped when its usage reached a budget."""
    return (
        f"Stopped because the {line.label.lower()} of ${line.limit:.2f} for this month was reached during this reply "
        f"(${line.spent + used:.2f}). Raise it in Settings, or use the Claude subscription connection."
    )
