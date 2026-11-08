"""The usage and cost overview of an environment.

Metered calls carry their cost. Calls through a subscription (Claude Code) cost nothing per call; for them the
overview shows what the same tokens would have cost at API prices, so the value of the subscription is visible.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.providers.base import TokenUsage
from app.providers.catalog import calculate_cost
from app.providers.registry import ProviderRegistry
from app.storage.models import Conversation, Environment, Project, UsageRecord

TOP_CONVERSATIONS = 10
MONTHS_SHOWN = 12


# How each role shows in the usage overview.
PURPOSE_LABELS = {"main": "Conversations", "worker": "Delegated work", "utility": "Utility", "embedding": "Embeddings"}


@dataclass
class UsageTotals:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    # What was charged per call (metered connections).
    cost: float = 0.0
    # What subscription calls would have cost at API prices.
    subscription_value: float = 0.0
    # Calls whose cost is unknown: metered, but the model has no price in the catalog.
    unpriced_calls: int = 0

    def add(self, record: UsageRecord, value: float | None) -> None:
        self.calls += 1
        self.input_tokens += record.input_tokens
        self.output_tokens += record.output_tokens
        self.cache_read_tokens += record.cache_read_tokens
        self.cache_write_tokens += record.cache_write_tokens
        if record.billing == "subscription":
            self.subscription_value += value or 0.0
        elif record.cost is not None:
            self.cost += record.cost
        else:
            self.unpriced_calls += 1


@dataclass
class UsageGroup:
    key: str
    label: str
    totals: UsageTotals = field(default_factory=UsageTotals)


@dataclass
class UsageReport:
    month: str
    months: list[UsageGroup]
    totals: UsageTotals
    by_model: list[UsageGroup]
    by_provider: list[UsageGroup]
    by_purpose: list[UsageGroup]
    by_billing: list[UsageGroup]
    top_conversations: list[UsageGroup]
    # Per project within one environment; per environment for the overview of all environments.
    by_project: list[UsageGroup] = field(default_factory=list)
    by_environment: list[UsageGroup] = field(default_factory=list)


def month_key(moment: datetime) -> str:
    return f"{moment.year:04d}-{moment.month:02d}"


def month_bounds(month: str) -> tuple[datetime, datetime]:
    year, number = (int(part) for part in month.split("-"))
    start = datetime(year, number, 1, tzinfo=UTC)
    end = datetime(year + (number == 12), number % 12 + 1, 1, tzinfo=UTC)
    return start, end


def api_value(record: UsageRecord, providers: ProviderRegistry) -> float | None:
    """What the call would cost at the catalog's API price."""
    model = providers.find_model(record.model)
    if model is None:
        return None
    usage = TokenUsage(
        input_tokens=record.input_tokens,
        output_tokens=record.output_tokens,
        cache_read_tokens=record.cache_read_tokens,
        cache_write_tokens=record.cache_write_tokens,
    )
    return calculate_cost(model, usage)


def sorted_groups(groups: dict[str, UsageGroup]) -> list[UsageGroup]:
    return sorted(
        groups.values(), key=lambda group: (group.totals.cost + group.totals.subscription_value, group.totals.calls), reverse=True
    )


def build_report(
    session: Session, environment_id: int | None, providers: ProviderRegistry, month: str | None = None
) -> UsageReport:
    """The usage of one environment, or of all environments when environment_id is None."""
    month = month or month_key(datetime.now(UTC))
    start, end = month_bounds(month)

    # Month series: the selected month and the eleven before it.
    first_month = start
    for _ in range(MONTHS_SHOWN - 1):
        first_month = (first_month - timedelta(days=1)).replace(day=1)
    months: dict[str, UsageGroup] = {}
    cursor = first_month
    while cursor < end:
        key = month_key(cursor)
        months[key] = UsageGroup(key, key)
        cursor = month_bounds(key)[1]

    query = select(UsageRecord).where(UsageRecord.created_at >= first_month, UsageRecord.created_at < end)
    if environment_id is not None:
        query = query.where(UsageRecord.environment_id == environment_id)
    records = session.scalars(query)
    project_of = dict(session.execute(select(Conversation.id, Conversation.project_id)).all())
    project_names = dict(session.execute(select(Project.id, Project.name)).all())
    environment_names = dict(session.execute(select(Environment.id, Environment.name)).all())
    by_project: dict[str, UsageGroup] = {}
    by_environment: dict[str, UsageGroup] = {}
    totals = UsageTotals()
    by_model: dict[str, UsageGroup] = {}
    by_purpose: dict[str, UsageGroup] = {}
    by_billing: dict[str, UsageGroup] = {}
    by_provider: dict[str, UsageGroup] = {}
    by_conversation: dict[str, UsageGroup] = {}
    for record in records:
        value = api_value(record, providers) if record.billing == "subscription" else None
        created = record.created_at if record.created_at.tzinfo else record.created_at.replace(tzinfo=UTC)
        months[month_key(created)].totals.add(record, value)
        if created < start:
            continue
        totals.add(record, value)
        model = providers.find_model(record.model)
        by_model.setdefault(record.model, UsageGroup(record.model, model.display_name if model else record.model)).totals.add(record, value)
        by_provider.setdefault(record.provider, UsageGroup(record.provider, providers.provider_label(record.provider))).totals.add(record, value)
        purpose_label = PURPOSE_LABELS.get(record.purpose, record.purpose.capitalize())
        by_purpose.setdefault(record.purpose, UsageGroup(record.purpose, purpose_label)).totals.add(record, value)
        billing_label = "Subscription" if record.billing == "subscription" else "API (metered)"
        by_billing.setdefault(record.billing, UsageGroup(record.billing, billing_label)).totals.add(record, value)
        if record.conversation_id is not None:
            key = str(record.conversation_id)
            by_conversation.setdefault(key, UsageGroup(key, "")).totals.add(record, value)
        if environment_id is None:
            key = str(record.environment_id)
            label = environment_names.get(record.environment_id, "Removed environment")
            by_environment.setdefault(key, UsageGroup(key, label)).totals.add(record, value)
        else:
            project_id = project_of.get(record.conversation_id) if record.conversation_id is not None else None
            key = str(project_id) if project_id is not None else "none"
            label = project_names.get(project_id, "Removed project") if project_id is not None else "Outside a project"
            by_project.setdefault(key, UsageGroup(key, label)).totals.add(record, value)

    top = sorted_groups(by_conversation)[:TOP_CONVERSATIONS]
    if top:
        titles = dict(
            session.execute(
                select(Conversation.id, Conversation.title).where(Conversation.id.in_([int(group.key) for group in top]))
            ).all()
        )
        for group in top:
            group.label = titles.get(int(group.key), "Removed conversation")

    return UsageReport(
        month=month,
        months=list(months.values()),
        totals=totals,
        by_model=sorted_groups(by_model),
        by_provider=sorted_groups(by_provider),
        by_purpose=sorted_groups(by_purpose),
        by_billing=sorted_groups(by_billing),
        top_conversations=top,
        by_project=sorted_groups(by_project),
        by_environment=sorted_groups(by_environment),
    )


