"""What YAAP's own helpers did: per background task its switch, model, this month's runs and cost and the last runs,
and per built-in Blueprint item what it is, who replaced it and what it costs.

Which model a task runs on is never decided here: pick_model() answers it for a request that makes no call.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.blueprint.builtin import BuiltinItem, builtin_items, can_override, overridden_by
from app.blueprint.levels import layers_for
from app.core.budget import month_start
from app.core.model_choice import ForUtility, pick_model
from app.core.settings import SETTING_DEFINITIONS, resolve_setting
from app.core.utility_tasks import OFF_SETTING_KEY, ROUTED_AS, SWITCHABLE_TASKS, TASK_LABELS, task_enabled
from app.providers.registry import ProviderRegistry
from app.storage.models import Environment, UsageRecord

StartedBy = Literal["yaap", "user"]

# Started by a button or a request of the user, not by YAAP itself.
USER_STARTED: frozenset[str] = frozenset({"rule", "import", "eval_case", "commit_message", "pull_request"})
# The rubric scorer chooses its own model, so no single model applies.
OWN_MODEL_TASKS: frozenset[str] = frozenset({"eval_rubric"})
# Summaries are written by the worker model, not the utility model.
WORKER_MODEL_TASKS: frozenset[str] = frozenset({"summary"})
LAST_RUNS = 5
WORKER_MODEL_NOTE = "The preset's worker model"
VARIES_NOTE = "Varies per task"


@dataclass(frozen=True)
class RunRow:
    created_at: datetime
    model: str
    cost: float | None
    summary: str | None
    conversation_id: int | None


@dataclass(frozen=True)
class TaskUsage:
    runs: int
    cost: float


@dataclass(frozen=True)
class TaskRow:
    task: str
    label: str
    switchable: bool
    enabled: bool
    started_by: StartedBy
    model: str | None
    runs: int
    cost: float
    last_runs: list[RunRow]
    builtin_item: str | None


@dataclass(frozen=True)
class OverrideRow:
    kind: str
    label: str


@dataclass(frozen=True)
class BuiltinRow:
    name: str
    kind: str
    path: str
    description: str
    when: str
    role: str | None
    tasks: list[str]
    instructions: str
    can_override: bool
    overridden_by: OverrideRow | None
    model: str | None
    model_note: str | None
    runs: int | None
    cost: float | None


def started_by(task: str) -> StartedBy:
    return "user" if task in USER_STARTED else "yaap"


def listed_tasks() -> list[str]:
    """The tasks with a row of their own: a combined call counts toward the task it is routed as."""
    return [task for task in TASK_LABELS if task not in ROUTED_AS]


def recorded_tasks(task: str) -> list[str]:
    """The task names under which a task's usage is recorded."""
    return [task, *(combined for combined, routed in ROUTED_AS.items() if routed == task)]


def builtin_item_for_task(task: str) -> str | None:
    """The built-in item whose instructions the task uses."""
    for name, item in builtin_items().items():
        if task in item.tasks:
            return name
    return None


def usage_by_task(session: Session, environment_id: int) -> dict[str, TaskUsage]:
    """This month's utility runs and cost per listed task, in the environment."""
    rows = session.execute(
        select(UsageRecord.task, func.count(), func.coalesce(func.sum(UsageRecord.cost), 0.0))
        .where(
            UsageRecord.environment_id == environment_id,
            UsageRecord.purpose == "utility",
            UsageRecord.created_at >= month_start(),
        )
        .group_by(UsageRecord.task)
    ).all()
    found = {task: (int(runs), float(cost)) for task, runs, cost in rows if task is not None}
    totals: dict[str, TaskUsage] = {}
    for task in listed_tasks():
        parts = [found[name] for name in recorded_tasks(task) if name in found]
        totals[task] = TaskUsage(sum(runs for runs, _ in parts), round(sum(cost for _, cost in parts), 6))
    return totals


def last_runs(session: Session, environment_id: int, task: str) -> list[RunRow]:
    records = session.scalars(
        select(UsageRecord)
        .where(
            UsageRecord.environment_id == environment_id,
            UsageRecord.purpose == "utility",
            UsageRecord.task.in_(recorded_tasks(task)),
        )
        .order_by(UsageRecord.created_at.desc(), UsageRecord.id.desc())
        .limit(LAST_RUNS)
    )
    return [RunRow(r.created_at, r.model, r.cost, r.summary, r.conversation_id) for r in records]


def task_model_id(session: Session, providers: ProviderRegistry, environment_id: int, task: str) -> str | None:
    """The model a task runs on, or None when no single model applies."""
    if task in OWN_MODEL_TASKS:
        return None
    request = ForUtility(role="worker") if task in WORKER_MODEL_TASKS else ForUtility(task=task)
    return pick_model(session, providers, environment_id, request).model


def tasks_off(session: Session, environment_id: int) -> list[str]:
    value = resolve_setting(session, SETTING_DEFINITIONS[OFF_SETTING_KEY], environment_id).value
    return [task for task in value if isinstance(task, str)] if isinstance(value, list) else []


def background_tasks(session: Session, providers: ProviderRegistry, environment_id: int) -> list[TaskRow]:
    off = tasks_off(session, environment_id)
    usage = usage_by_task(session, environment_id)
    return [
        TaskRow(
            task=task,
            label=TASK_LABELS[task],
            switchable=task in SWITCHABLE_TASKS,
            enabled=task_enabled(task, off),
            started_by=started_by(task),
            model=task_model_id(session, providers, environment_id, task),
            runs=usage[task].runs,
            cost=usage[task].cost,
            last_runs=last_runs(session, environment_id, task),
            builtin_item=builtin_item_for_task(task),
        )
        for task in listed_tasks()
    ]


def item_model(
    session: Session, providers: ProviderRegistry, environment_id: int, item: BuiltinItem
) -> tuple[str | None, str | None]:
    """The model an item runs on and, when there is no single one, a note saying which applies."""
    if not item.tasks:
        return None, WORKER_MODEL_NOTE
    models = {task_model_id(session, providers, environment_id, task) for task in item.tasks}
    if len(models) == 1 and None not in models:
        return next(iter(models)), None
    return None, VARIES_NOTE


def item_usage(item: BuiltinItem, usage: dict[str, TaskUsage]) -> tuple[int | None, float | None]:
    """Runs and cost of an item's tasks; None for a worker or explorer, whose delegated runs are recorded without
    the agent that ran them."""
    known = [usage[task] for task in item.tasks if task in usage]
    if not known:
        return None, None
    return sum(u.runs for u in known), round(sum(u.cost for u in known), 6)


def builtin_rows(
    session: Session,
    providers: ProviderRegistry,
    environment: Environment,
    blueprint_dir: Path,
    workspace_dir: Path,
) -> list[BuiltinRow]:
    layers = layers_for(blueprint_dir, workspace_dir, environment)
    usage = usage_by_task(session, environment.id)
    rows = []
    for name, item in builtin_items().items():
        layer = overridden_by(name, layers)
        model, note = item_model(session, providers, environment.id, item)
        runs, cost = item_usage(item, usage)
        rows.append(
            BuiltinRow(
                name=name,
                kind=item.kind,
                path=item.path,
                description=item.description,
                when=item.when,
                role=item.role,
                tasks=list(item.tasks),
                instructions=item.body,
                can_override=can_override(name),
                overridden_by=OverrideRow(layer.kind, layer.label) if layer is not None else None,
                model=model,
                model_note=note,
                runs=runs,
                cost=cost,
            )
        )
    return rows
