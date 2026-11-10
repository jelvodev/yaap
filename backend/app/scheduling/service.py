"""Running scheduled tasks.

A loop checks every LOOP_SECONDS. A due task is claimed with one update that only succeeds while no run of it goes
on, so a task never runs twice at once. A run missed while YAAP was down runs once when it starts, and the next time
is counted from then. A run is a goal-mode run in a conversation of the project (a new one per run, or always the
same one), without the user: tools that would ask are refused unless the task allows them. The run ends with its
goal; its status, summary and cost are kept, and the conversation is marked unread.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import anyio
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.audit import prune_audit
from app.core.chat import add_user_message
from app.core.goals import ACTIVE, OPEN_STATUSES, create_goal, end_goal, goal_block, goal_cost, open_goal, start_text
from app.core.reply_errors import ReplyError
from app.core.reply_service import ReplyRequest, ReplyService
from app.core.reply_services import ReplyServices
from app.backups.runs import backup_tick
from app.updates.background import update_tick
from app.reviews.service import ReviewService
from app.vault.store import prune_uses_daily
from app.scheduling.active import RUNNING
from app.scheduling.cron import CronError, next_time, parse
from app.scheduling.eval_action import EVAL_ACTION, finish_eval_record, start_eval
from app.evals.runs import prune_workspaces as prune_eval_workspaces
from app.storage.models import Conversation, Goal, Project, ScheduledRun, ScheduledTask
from app.tracing.snapshots import prune_traces_daily

logger = logging.getLogger(__name__)

LOOP_SECONDS = 60
MAX_MOMENTS = 50


class TaskError(Exception):
    """A task that cannot be saved or run; the message is safe to show."""


def parse_moment(text: str) -> datetime:
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as error:
        raise TaskError(f"'{text}' is not a date and time such as 2026-12-01T09:00+01:00.") from error
    if moment.tzinfo is None:
        raise TaskError(f"'{text}' needs a time zone offset, such as +01:00.")
    return moment.astimezone(UTC)


def check_schedule(kind: str, cron: str, timezone: str, moments: list[str]) -> None:
    if kind == "cron":
        try:
            parsed = parse(cron)
            if next_time(parsed, timezone, datetime.now(UTC)) is None:
                raise TaskError("This schedule never comes.")
        except CronError as error:
            raise TaskError(str(error)) from error
    elif kind == "moments":
        if not moments or len(moments) > MAX_MOMENTS:
            raise TaskError(f"Give one to {MAX_MOMENTS} moments.")
        for moment in moments:
            parse_moment(moment)
    elif kind != "manual":
        raise TaskError("The schedule is cron, moments or manual.")


def upcoming(task: ScheduledTask, after: datetime, count: int = 3) -> list[datetime]:
    """The next times the task runs after `after`."""
    times: list[datetime] = []
    if task.schedule_kind == "cron":
        try:
            cron = parse(task.cron)
        except CronError:
            return []
        moment = after
        while len(times) < count:
            found = next_time(cron, task.timezone, moment)
            if found is None:
                break
            times.append(found)
            moment = found
    elif task.schedule_kind == "moments":
        times = sorted(moment for moment in (parse_moment(text) for text in task.moments) if moment > after)[:count]
    return times


def schedule_next(task: ScheduledTask, after: datetime) -> None:
    times = upcoming(task, after, 1)
    task.next_run_at = times[0] if times and task.enabled else None


def claim(session: Session, task: ScheduledTask, trigger: str, now: datetime) -> bool:
    """Take the task for a run in one update; False when a run goes on or someone else took it."""
    query = update(ScheduledTask).where(ScheduledTask.id == task.id, ScheduledTask.running.is_(False))
    if trigger != "manual":
        query = query.where(ScheduledTask.next_run_at == task.next_run_at)
    following = upcoming(task, now, 1) if task.enabled else []
    result = session.execute(
        query.values(running=True, last_run_at=now, next_run_at=following[0] if following else None)
        .execution_options(synchronize_session=False)
    )
    session.commit()
    return result.rowcount == 1


def due_tasks(session: Session, now: datetime) -> list[ScheduledTask]:
    return list(
        session.scalars(
            select(ScheduledTask).where(
                ScheduledTask.enabled.is_(True),
                ScheduledTask.running.is_(False),
                ScheduledTask.next_run_at.is_not(None),
                ScheduledTask.next_run_at <= now,
            )
        )
    )


def run_conversation(session: Session, task: ScheduledTask, now: datetime) -> Conversation:
    project = session.get_one(Project, task.project_id)
    if task.conversation_mode == "one" and task.conversation_id is not None:
        existing = session.get(Conversation, task.conversation_id)
        if existing is not None and existing.project_id == task.project_id:
            goal = open_goal(session, existing.id)
            if goal is not None:
                end_goal(goal, "stopped", "A new run of the scheduled task started.")
            existing.archived_at = None
            return existing
    conversation = Conversation(
        environment_id=project.environment_id,
        project_id=project.id,
        agent=task.agent,
        pinned_skills=[],
        preset=task.preset,
        title=f"{task.name} · {now:%Y-%m-%d %H:%M}" if task.conversation_mode == "new" else task.name,
        provider="",
        model=task.model,
        effort=task.effort,
        scheduled_task_id=task.id,
        created_at=now,
        updated_at=now,
    )
    session.add(conversation)
    session.flush()
    if task.conversation_mode == "one":
        task.conversation_id = conversation.id
    return conversation


@dataclass
class Started:
    run_id: int
    # None for an eval run, which plays its cases in conversations of its own.
    conversation_id: int | None


def start_run(app: Any, task_id: int, trigger: str) -> Started | None:
    """Start a claimed task's run: its conversation, goal and first turn. Runs in a worker thread."""
    state = app.state
    service = ReplyService(ReplyServices.from_state(state))
    now = datetime.now(UTC)
    with state.session_factory() as session:
        task = session.get(ScheduledTask, task_id)
        if task is None:
            return None
        record = ScheduledRun(task_id=task.id, trigger=trigger, status=RUNNING, started_at=now)
        session.add(record)
        if task.action == EVAL_ACTION:
            session.commit()
            problem = start_eval(app, session, task, record)
            if problem is not None:
                fail(session, task, record, problem)
                return None
            return Started(record.id, None)
        model = state.providers.find_model(task.model)
        if model is None:
            fail(session, task, record, f"The model {task.model} is not available.")
            return None
        conversation = run_conversation(session, task, now)
        conversation.provider = model.provider
        conversation.model = model.id
        conversation.updated_at = now
        record.conversation_id = conversation.id
        replies = state.replies
        if replies.running(conversation.id) is not None:
            fail(session, task, record, "The task's conversation was still busy.")
            return None
        try:
            goal = create_goal(session, conversation, task.prompt)
        except Exception as error:  # noqa: BLE001 - a GoalError, shown in the run
            fail(session, task, record, str(error))
            return None
        goal.max_turns, goal.max_cost = task.max_turns, task.max_cost
        record.goal_id = goal.id
        session.commit()
        try:
            setup = service.prepare(session, conversation, task.prompt)
        except ReplyError as error:
            end_goal(goal, "stopped", str(error))
            fail(session, task, record, str(error))
            return None
        block = goal_block(start_text(task.prompt, goal.max_turns), 1, goal.max_turns)
        user_message = add_user_message(session, conversation, "", extra_blocks=[block])
        started = Started(record.id, conversation.id)
        service.launch(session, setup, ReplyRequest(conversation, user_message, unattended=True))
        return started


def fail(session: Session, task: ScheduledTask, record: ScheduledRun, reason: str) -> None:
    record.status, record.summary, record.finished_at = "failed", reason, datetime.now(UTC)
    task.running = False
    session.commit()
    logger.warning("Scheduled task %s did not run: %s", task.id, reason)


def finish_runs(app: Any) -> int:
    """End the runs whose goal ended (or paused) and whose reply is done. Returns how many ended."""
    state = app.state
    ended = 0
    ended_goals: list[int] = []
    with state.session_factory() as session:
        for record in session.scalars(select(ScheduledRun).where(ScheduledRun.status == RUNNING)).all():
            task = session.get(ScheduledTask, record.task_id)
            if task is not None and task.action == EVAL_ACTION:
                ended += finish_eval_record(session, task, record)
                continue
            goal = session.get(Goal, record.goal_id) if record.goal_id else None
            if record.conversation_id is not None and state.replies.running(record.conversation_id) is not None:
                continue
            if goal is not None and goal.status == ACTIVE:
                continue
            record.status = goal.status if goal is not None else "failed"
            record.summary = goal.summary if goal is not None else "The run lost its goal."
            record.cost = goal_cost(session, goal) if goal is not None else 0.0
            record.finished_at = datetime.now(UTC)
            if goal is not None and goal.status in OPEN_STATUSES:
                # Paused without the user (a repeated call, a stopped reply): the run ends here.
                end_goal(goal, "stopped", goal.summary or "The run paused.")
            if goal is not None:
                ended_goals.append(goal.id)
            if task is not None:
                task.running = False
            conversation = session.get(Conversation, record.conversation_id) if record.conversation_id else None
            if conversation is not None:
                conversation.unread = True
            ended += 1
        session.commit()
    # A run that changed code gets a review by another vendor; a goal is reviewed only once.
    reviews = ReviewService(ReplyServices.from_state(state))
    for goal_id in ended_goals:
        reviews.after_goal_from_thread(goal_id)
    return ended


def tick(app: Any, now: datetime | None = None) -> list[Started]:
    """One round of the loop: end finished runs, then start the due ones. Runs in a worker thread."""
    finish_runs(app)
    with app.state.session_factory() as session:
        prune_uses_daily(session)
        prune_audit(session)
    prune_traces_daily(app.state.data_dir)
    with app.state.session_factory() as session:
        prune_eval_workspaces(session, app.state.workspace_dir)
    backup_tick(app)
    update_tick(app.state.updates)
    now = now or datetime.now(UTC)
    started = []
    with app.state.session_factory() as session:
        due = due_tasks(session, now)
        claimed = []
        for task in due:
            # A run due more than two loop rounds ago was missed while YAAP was down; it runs once now.
            trigger = "missed" if task.next_run_at and (now - task.next_run_at).total_seconds() > 2 * LOOP_SECONDS else "schedule"
            if claim(session, task, trigger, now):
                claimed.append((task.id, trigger))
    for task_id, trigger in claimed:
        try:
            result = start_run(app, task_id, trigger)
        except Exception:
            logger.exception("Starting scheduled task %s failed.", task_id)
            release(app, task_id)
            continue
        if result is not None:
            started.append(result)
    return started


def release(app: Any, task_id: int) -> None:
    with app.state.session_factory() as session:
        task = session.get(ScheduledTask, task_id)
        if task is not None:
            task.running = False
            session.commit()


def run_now(app: Any, task_id: int) -> Started | None:
    with app.state.session_factory() as session:
        task = session.get(ScheduledTask, task_id)
        if task is None:
            raise TaskError("Task not found.")
        if not claim(session, task, "manual", datetime.now(UTC)):
            raise TaskError("A run of this task is still going on.")
    try:
        return start_run(app, task_id, "manual")
    except Exception:
        release(app, task_id)
        raise


def recover_runs(app: Any) -> int:
    """Runs that were going on when YAAP stopped cannot go on: they end as stopped and their tasks are free again."""
    with app.state.session_factory() as session:
        records = session.scalars(select(ScheduledRun).where(ScheduledRun.status == RUNNING)).all()
        for record in records:
            goal = session.get(Goal, record.goal_id) if record.goal_id else None
            if goal is not None and goal.status in OPEN_STATUSES:
                end_goal(goal, "stopped", "YAAP restarted during the run.")
            record.status, record.finished_at = "stopped", datetime.now(UTC)
            record.summary = "YAAP restarted during the run."
            record.cost = goal_cost(session, goal) if goal is not None else 0.0
        session.execute(update(ScheduledTask).where(ScheduledTask.running.is_(True)).values(running=False))
        session.commit()
        return len(records)


async def run_periodically(app: Any, interval: float = LOOP_SECONDS) -> None:
    while True:
        try:
            await anyio.to_thread.run_sync(tick, app)
        except Exception:
            logger.exception("The scheduled task loop failed.")
        await asyncio.sleep(interval)
