"""Usage models: spend summaries, budgets and the monthly usage report."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class BudgetOut(BaseModel):
    scope: Literal["environment", "total"]
    label: str
    limit: float
    spent: float


class UsageSummaryOut(BaseModel):
    period_start: datetime
    cost: float
    priced_records: int
    unpriced_records: int
    # Replies covered by a subscription (Claude Code): no per-reply cost.
    subscription_records: int
    input_tokens: int
    output_tokens: int
    budgets: list[BudgetOut] = Field(default_factory=list)


class UsageTotalsOut(BaseModel):
    calls: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    # Charged per call on metered connections.
    cost: float
    # What subscription calls would have cost at API prices.
    subscription_value: float
    # Metered calls without a known price.
    unpriced_calls: int


class UsageGroupOut(BaseModel):
    key: str
    label: str
    totals: UsageTotalsOut


class UsageReportOut(BaseModel):
    month: str
    months: list[UsageGroupOut]
    totals: UsageTotalsOut
    by_model: list[UsageGroupOut]
    by_provider: list[UsageGroupOut]
    by_purpose: list[UsageGroupOut]
    by_billing: list[UsageGroupOut]
    top_conversations: list[UsageGroupOut]
    by_project: list[UsageGroupOut]
    by_environment: list[UsageGroupOut]
