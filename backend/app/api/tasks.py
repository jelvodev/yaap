"""Scheduled tasks of a project: create, change, pause, run now, and their runs."""

from datetime import UTC, datetime
from typing import Literal

import anyio
from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.agent.policy import check_commands, check_domains
from app.api.dependencies import EnvironmentDependency, ProjectDependency, SessionDependency
from app.board.triggers import TriggerConfig, trigger_refusal
from app.core.settings import validate_timezone
from app.scheduling.board_trigger import cards_this_week
from app.scheduling.cron import CronError, describe, parse
from app.scheduling.service import TaskError, check_schedule, run_now, schedule_next, upcoming
from app.storage.models import Conversation, Project, ScheduledRun, ScheduledTask

router = APIRouter(tags=["tasks"])

MAX_TASKS = 50


class TaskIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    # What a run does: a goal-mode run of the prompt, an eval run of the project's cases (all, or eval_case), or a
    # board trigger that polls and makes cards in Inbox without a model (config; model and effort are not used).
    action: Literal["prompt", "eval", "board_trigger"] = "prompt"
    prompt: str = Field(default="", max_length=20_000)
    eval_case: str | None = Field(default=None, max_length=60)
    config: TriggerConfig | None = None
    model: str
    effort: str
    agent: str | None = None
    preset: str | None = None
    schedule_kind: Literal["cron", "moments", "manual"]
    cron: str = ""
    timezone: str = "UTC"
    moments: list[str] = Field(default_factory=list)
    max_turns: int = Field(default=10, ge=1, le=100)
    max_cost: float = Field(default=2.0, ge=0, le=1000)
    allowed_commands: list[str] = Field(default_factory=list)
    allowed_domains: list[str] = Field(default_factory=list)
    conversation_mode: Literal["new", "one"] = "new"
    enabled: bool = True


class TaskOut(TaskIn):
    id: int
    project_id: int
    schedule_text: str
    next_runs: list[datetime]
    running: bool
    last_run_at: datetime | None
    conversation_id: int | None
    # For a board trigger: the cards its runs made in the last seven days.
    cards_this_week: int = 0


class TaskRunOut(BaseModel):
    id: int
    task_id: int
    task_name: str
    project_id: int
    conversation_id: int | None
    trigger: str
    status: str
    summary: str | None
    cost: float
    started_at: datetime
    finished_at: datetime | None


def schedule_text(task: ScheduledTask) -> str:
    if task.schedule_kind == "manual":
        return "On demand"
    if task.schedule_kind == "moments":
        return f"At {len(task.moments)} set moment{'s' if len(task.moments) != 1 else ''}"
    try:
        return describe(parse(task.cron), task.timezone)
    except CronError:
        return task.cron


def task_out(task: ScheduledTask, cards: int = 0) -> TaskOut:
    return TaskOut(
        id=task.id,
        project_id=task.project_id,
        name=task.name,
        action=task.action,  # type: ignore[arg-type]  # stored as one of the Literal's values
        prompt=task.prompt,
        eval_case=task.eval_case,
        config=TriggerConfig.model_validate(task.config) if task.config else None,
        model=task.model,
        effort=task.effort,
        agent=task.agent,
        preset=task.preset,
        schedule_kind=task.schedule_kind,  # type: ignore[arg-type]
        cron=task.cron,
        timezone=task.timezone,
        moments=task.moments or [],
        max_turns=task.max_turns,
        max_cost=task.max_cost,
        allowed_commands=task.allowed_commands or [],
        allowed_domains=task.allowed_domains or [],
        conversation_mode=task.conversation_mode,  # type: ignore[arg-type]
        enabled=task.enabled,
        schedule_text=schedule_text(task),
        next_runs=upcoming(task, datetime.now(UTC)) if task.enabled else [],
        running=task.running,
        last_run_at=task.last_run_at,
        conversation_id=task.conversation_id,
        cards_this_week=cards,
    )


def run_out(run: ScheduledRun, task: ScheduledTask) -> TaskRunOut:
    return TaskRunOut(
        id=run.id,
        task_id=task.id,
        task_name=task.name,
        project_id=task.project_id,
        conversation_id=run.conversation_id,
        trigger=run.trigger,
        status=run.status,
        summary=run.summary,
        cost=run.cost,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


def model_and_effort(payload: TaskIn, request: Request) -> tuple[str, str]:
    """The task's model and effort; a board trigger makes no model call, so it keeps what it was given."""
    if payload.action == "board_trigger":
        return payload.model, payload.effort
    model = request.app.state.providers.find_model(payload.model)
    if model is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown model.")
    if payload.effort not in model.efforts:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Effort must be one of: {', '.join(model.efforts)}.")
    return model.id, payload.effort


def action_problem(payload: TaskIn, allowed_commands: list[str]) -> str | None:
    if payload.action == "prompt" and not payload.prompt.strip():
        return "Give the prompt the task runs."
    if payload.action == "board_trigger":
        if payload.config is None:
            return "Give the trigger's settings."
        return trigger_refusal(payload.config, allowed_commands)
    return None


def apply(task: ScheduledTask, payload: TaskIn, request: Request) -> None:
    model_id, effort = model_and_effort(payload, request)
    allowed_commands = [" ".join(command.split()) for command in payload.allowed_commands if command.strip()]
    problem = validate_timezone(payload.timezone, None) or check_commands(payload.allowed_commands) or check_domains(payload.allowed_domains)
    problem = problem or action_problem(payload, allowed_commands)
    if problem:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, problem)
    try:
        check_schedule(payload.schedule_kind, payload.cron.strip(), payload.timezone or "UTC", payload.moments)
    except TaskError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    if task.conversation_mode != payload.conversation_mode:
        task.conversation_id = None
    task.name, task.prompt = payload.name.strip(), payload.prompt.strip()
    task.action = payload.action
    task.eval_case = ((payload.eval_case or "").strip() or None) if payload.action == "eval" else None
    task.config = payload.config.model_dump() if payload.action == "board_trigger" and payload.config else None
    task.model, task.effort = model_id, effort
    task.agent, task.preset = payload.agent or None, payload.preset or None
    task.schedule_kind, task.cron = payload.schedule_kind, " ".join(payload.cron.split())
    task.timezone, task.moments = payload.timezone or "UTC", payload.moments
    task.max_turns, task.max_cost = payload.max_turns, payload.max_cost
    task.allowed_commands = allowed_commands
    task.allowed_domains = [domain.strip().lower() for domain in payload.allowed_domains if domain.strip()]
    task.conversation_mode, task.enabled = payload.conversation_mode, payload.enabled
    schedule_next(task, datetime.now(UTC))


def find_task(session: SessionDependency, project: Project, task_id: int) -> ScheduledTask:
    task = session.get(ScheduledTask, task_id)
    if task is None or task.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found.")
    return task


@router.get("/projects/{project_id}/tasks", response_model=list[TaskOut])
def list_tasks(project: ProjectDependency, session: SessionDependency) -> list[TaskOut]:
    tasks = session.scalars(select(ScheduledTask).where(ScheduledTask.project_id == project.id).order_by(ScheduledTask.name))
    return [task_out(task, cards_this_week(session, task.id) if task.action == "board_trigger" else 0) for task in tasks]


@router.post("/projects/{project_id}/tasks", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
def create_task(payload: TaskIn, project: ProjectDependency, session: SessionDependency, request: Request) -> TaskOut:
    count = len(session.scalars(select(ScheduledTask.id).where(ScheduledTask.project_id == project.id)).all())
    if count >= MAX_TASKS:
        raise HTTPException(status.HTTP_409_CONFLICT, f"A project has at most {MAX_TASKS} tasks.")
    task = ScheduledTask(project_id=project.id, running=False, conversation_mode=payload.conversation_mode, created_at=datetime.now(UTC))
    apply(task, payload, request)
    session.add(task)
    session.commit()
    return task_out(task)


@router.put("/projects/{project_id}/tasks/{task_id}", response_model=TaskOut)
def update_task(task_id: int, payload: TaskIn, project: ProjectDependency, session: SessionDependency, request: Request) -> TaskOut:
    task = find_task(session, project, task_id)
    apply(task, payload, request)
    session.commit()
    return task_out(task)


@router.delete("/projects/{project_id}/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(task_id: int, project: ProjectDependency, session: SessionDependency) -> Response:
    """Remove the task and its run records; the conversations of its runs stay."""
    task = find_task(session, project, task_id)
    if task.running:
        raise HTTPException(status.HTTP_409_CONFLICT, "A run of this task is going on; wait until it ends.")
    for run in session.scalars(select(ScheduledRun).where(ScheduledRun.task_id == task.id)):
        session.delete(run)
    for conversation in session.scalars(select(Conversation).where(Conversation.scheduled_task_id == task.id)):
        conversation.scheduled_task_id = None
    session.delete(task)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/projects/{project_id}/tasks/{task_id}/run", response_model=TaskRunOut, status_code=status.HTTP_202_ACCEPTED)
async def run_task_now(task_id: int, project: ProjectDependency, session: SessionDependency, request: Request) -> TaskRunOut:
    task_id = find_task(session, project, task_id).id
    session.close()
    try:
        started = await anyio.to_thread.run_sync(run_now, request.app, task_id)
    except TaskError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    with request.app.state.session_factory() as fresh:
        task = fresh.get_one(ScheduledTask, task_id)
        run = (
            fresh.get_one(ScheduledRun, started.run_id)
            if started
            else fresh.scalar(select(ScheduledRun).where(ScheduledRun.task_id == task_id).order_by(ScheduledRun.id.desc()))
        )
        return run_out(run, task)


@router.get("/projects/{project_id}/tasks/{task_id}/runs", response_model=list[TaskRunOut])
def list_runs(task_id: int, project: ProjectDependency, session: SessionDependency) -> list[TaskRunOut]:
    task = find_task(session, project, task_id)
    runs = session.scalars(select(ScheduledRun).where(ScheduledRun.task_id == task.id).order_by(ScheduledRun.id.desc()).limit(50))
    return [run_out(run, task) for run in runs]


@router.get("/environments/{environment_id}/task-runs", response_model=list[TaskRunOut])
def recent_runs(environment: EnvironmentDependency, session: SessionDependency, since: datetime | None = None) -> list[TaskRunOut]:
    """Runs of the environment's tasks that finished since a moment, for notifications."""
    query = (
        select(ScheduledRun, ScheduledTask)
        .join(ScheduledTask, ScheduledTask.id == ScheduledRun.task_id)
        .join(Project, Project.id == ScheduledTask.project_id)
        .where(Project.environment_id == environment.id, ScheduledRun.finished_at.is_not(None))
    )
    if since is not None:
        query = query.where(ScheduledRun.finished_at > since)
    rows = session.execute(query.order_by(ScheduledRun.finished_at.desc()).limit(20)).all()
    return [run_out(run, task) for run, task in rows]
