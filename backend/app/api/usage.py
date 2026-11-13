from datetime import UTC, datetime

from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.dependencies import EnvironmentDependency, ProviderRegistryDependency, SessionDependency
from app.api.schemas import BudgetOut, UsageReportOut, UsageSummaryOut
from app.core.budget import budget_lines
from app.core.usage_report import build_report
from app.storage.models import UsageRecord

router = APIRouter(tags=["usage"])


def start_of_month(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


@router.get("/environments/{environment_id}/usage", response_model=UsageSummaryOut)
def environment_usage_this_month(environment: EnvironmentDependency, session: SessionDependency) -> UsageSummaryOut:
    period_start = start_of_month(datetime.now(UTC))
    row = session.execute(
        select(
            func.coalesce(func.sum(UsageRecord.cost), 0.0),
            func.count(UsageRecord.cost),
            func.count(),
            func.count().filter(UsageRecord.billing == "subscription"),
            func.coalesce(func.sum(UsageRecord.input_tokens + UsageRecord.cache_read_tokens + UsageRecord.cache_write_tokens), 0),
            func.coalesce(func.sum(UsageRecord.output_tokens), 0),
        ).where(UsageRecord.environment_id == environment.id, UsageRecord.created_at >= period_start)
    ).one()
    cost, priced, total, subscription, input_tokens, output_tokens = row
    return UsageSummaryOut(
        period_start=period_start,
        cost=round(float(cost), 6),
        priced_records=priced,
        unpriced_records=total - priced - subscription,
        subscription_records=subscription,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        budgets=[
            BudgetOut(
                scope=line.scope,  # type: ignore[arg-type]  # a plain string in the service; the schema narrows it
                label=line.label,
                limit=line.limit,
                spent=round(line.spent, 6),
            )
            for line in budget_lines(session, environment.id)
        ],
    )


@router.get("/environments/{environment_id}/usage/report", response_model=UsageReportOut)
def environment_usage_report(
    environment: EnvironmentDependency,
    session: SessionDependency,
    providers: ProviderRegistryDependency,
    month: str | None = Query(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
) -> UsageReportOut:
    if month is not None and month > f"{datetime.now(UTC):%Y-%m}":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That month is in the future.")
    return UsageReportOut.model_validate(asdict(build_report(session, environment.id, providers, month)))


@router.get("/usage/report", response_model=UsageReportOut)
def usage_report_all_environments(
    session: SessionDependency,
    providers: ProviderRegistryDependency,
    month: str | None = Query(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
) -> UsageReportOut:
    """The usage of all environments together, split per environment."""
    if month is not None and month > f"{datetime.now(UTC):%Y-%m}":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That month is in the future.")
    return UsageReportOut.model_validate(asdict(build_report(session, None, providers, month)))
