"""The background tasks of an environment and the work YAAP starts on a schedule."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.api import builtin_blueprint
from app.api.dependencies import EnvironmentDependency, ProviderRegistryDependency, SessionDependency
from app.core.background_tasks import background_tasks
from app.core.scheduled_work import scheduled_work

router = APIRouter(tags=["background-tasks"])
router.include_router(builtin_blueprint.router)


class BackgroundRunOut(BaseModel):
    created_at: datetime
    model: str
    cost: float | None
    summary: str | None
    conversation_id: int | None


class BackgroundTaskOut(BaseModel):
    task: str
    label: str
    # Whether the task can be switched off (the setting utility_tasks_off).
    switchable: bool
    enabled: bool
    started_by: Literal["yaap", "user"]
    # None when no single model applies.
    model: str | None
    # This month, in the environment.
    runs: int
    cost: float
    last_runs: list[BackgroundRunOut]
    # The built-in Blueprint item that holds the task's instructions.
    builtin_item: str | None


class ScheduledWorkOut(BaseModel):
    name: str
    description: str
    schedule: str
    enabled: bool
    setting_key: str | None


class BackgroundTasksOut(BaseModel):
    tasks: list[BackgroundTaskOut]
    scheduled: list[ScheduledWorkOut]


@router.get("/environments/{environment_id}/background-tasks", response_model=BackgroundTasksOut)
def environment_background_tasks(
    environment: EnvironmentDependency, session: SessionDependency, providers: ProviderRegistryDependency
) -> BackgroundTasksOut:
    tasks = background_tasks(session, providers, environment.id)
    return BackgroundTasksOut(
        tasks=[BackgroundTaskOut.model_validate(task, from_attributes=True) for task in tasks],
        scheduled=[
            ScheduledWorkOut.model_validate(work, from_attributes=True)
            for work in scheduled_work(session, environment.id)
        ],
    )
