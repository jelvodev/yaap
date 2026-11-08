"""Cost estimates from earlier similar runs: before a goal starts and on a scheduled task."""

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.dependencies import ProjectDependency, SessionDependency
from app.api.tasks import find_task
from app.tracing.estimate import MIN_RUNS, RunTotal, estimate
from app.tracing.history import goal_history, task_history

router = APIRouter(tags=["estimates"])


class CostEstimateOut(BaseModel):
    # How many earlier similar runs were found (at most the 20 most recent).
    runs: int
    # How many are needed for an estimate.
    min_runs: int
    # US dollars; empty while there are too few runs. The high value is the 90th percentile by nearest rank.
    median_cost: float | None
    high_cost: float | None
    median_tokens: int | None
    high_tokens: int | None


def estimate_out(totals: list[RunTotal]) -> CostEstimateOut:
    found = estimate(totals)
    return CostEstimateOut(
        runs=len(totals),
        min_runs=MIN_RUNS,
        median_cost=found.median_cost if found else None,
        high_cost=found.high_cost if found else None,
        median_tokens=found.median_tokens if found else None,
        high_tokens=found.high_tokens if found else None,
    )


@router.get("/projects/{project_id}/goal-estimate", response_model=CostEstimateOut)
def goal_estimate(project: ProjectDependency, session: SessionDependency, agent: str | None = None) -> CostEstimateOut:
    """What a goal in the project, worked on as the agent (none for plain YAAP), is likely to cost."""
    return estimate_out(goal_history(session, project.id, agent or None))


@router.get("/projects/{project_id}/tasks/{task_id}/estimate", response_model=CostEstimateOut)
def task_estimate(task_id: int, project: ProjectDependency, session: SessionDependency) -> CostEstimateOut:
    """What a run of the scheduled task is likely to cost."""
    return estimate_out(task_history(session, find_task(session, project, task_id).id))
