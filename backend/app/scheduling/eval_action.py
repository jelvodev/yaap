"""The *eval* action of a scheduled task: a run of the project's eval cases instead of a prompt.

The task's run record follows the eval run: it is running while the eval run is, and ends with its status, its
summary ("3 of 4 passed") and the cost of its plays.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.evals.runs import OPEN_RUN, EvalError, EvalService, RunStart
from app.storage.models import EvalResult, EvalRun, ScheduledRun, ScheduledTask

EVAL_ACTION = "eval"


def start_eval(app: Any, session: Session, task: ScheduledTask, record: ScheduledRun) -> str | None:
    """Start the task's eval run; the reason when it cannot start. Runs in a worker thread; record is committed."""
    service: EvalService = app.state.evals
    try:
        run_id = service.start(RunStart(task.project_id, "schedule", task.eval_case, scheduled_run_id=record.id))
    except EvalError as error:
        return str(error)
    record.summary = f"Eval run {run_id} started."
    session.commit()
    return None


def eval_outcome(session: Session, record: ScheduledRun) -> tuple[str, str, float] | None:
    """The status, summary and cost the task's run ends with; None while its eval run goes on."""
    run = session.scalar(select(EvalRun).where(EvalRun.scheduled_run_id == record.id))
    if run is None:
        return "failed", record.summary or "The eval run is gone.", 0.0
    if run.status in OPEN_RUN:
        return None
    cost = session.scalar(select(func.coalesce(func.sum(EvalResult.cost), 0.0)).where(EvalResult.run_id == run.id))
    status = {"done": "done", "stopped": "stopped"}.get(run.status, "failed")
    return status, run.summary or "", float(cost or 0.0)


def finish_eval_record(session: Session, task: ScheduledTask | None, record: ScheduledRun) -> bool:
    """End the record when its eval run ended; whether it did."""
    outcome = eval_outcome(session, record)
    if outcome is None:
        return False
    record.status, record.summary, record.cost = outcome
    record.finished_at = datetime.now(UTC)
    if task is not None:
        task.running = False
    return True
