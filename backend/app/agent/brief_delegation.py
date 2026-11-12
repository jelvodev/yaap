"""Delegation by brief: a checkable task goes to the worker, YAAP runs the check and a failure escalates.

The worker's own claim that its work passes is never the evidence. YAAP runs the brief's check itself, through the
permission gate of the run that delegated, so a mode that asks before commands asks here too. A failed check sends the
task back to the worker once, with the failure; a second failure hands it to the main model with the worker's report
and diff (agent/escalation.py decides who is next). Each step is noted on the lead's trace.
"""

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal

import anyio
from sqlalchemy.exc import SQLAlchemyError

from app.agent.brief import Brief, brief_message, retry_message, takeover_message
from app.agent.call_notes import record_note
from app.agent.escalation import Attempt, AttemptOutcome, NextAttempt, Role, check_outcome, next_attempt
from app.agent.gate import call_tool
from app.agent.git_tools import git_place_of
from app.agent.runs import AgentRun, RunRegistry
from app.agent.sub_run_report import CheckResult, capped, files_diff, lead_report, parse_report
from app.core.why import Why
from app.git.service import GitError, repository_diff
from app.storage.models import AgentRunRecord
from app.targets.base import TargetError
from app.tracing.notes import Note

if TYPE_CHECKING:  # Only for annotations: the delegator imports this module.
    from app.agent.delegation import Delegator, SubRunResult

logger = logging.getLogger(__name__)

CHECK_TIMEOUT_SECONDS = 600
MAX_LOGGED_CHECK_OUTPUT = 2000
EscalationStep = Literal["worker_retry", "main"]
_OUTCOME_WORDS: dict[AttemptOutcome, str] = {
    "passed": "check passed",
    "failed": "check failed",
    "not_run": "check could not run",
    "stopped": "stopped",
}
ESCALATION_WHY = Why(
    "A failed check goes back to the worker once; a second failure hands the brief to the main model.",
    "delegation",
    "delegation",
)


async def run_check(runs: RunRegistry, parent: AgentRun, command: str) -> CheckResult:
    """Run the brief's check as a command of the delegating run, through the permission gate and onto its trace."""
    output, is_error = await call_tool(
        runs, parent, "run_command", {"command": command, "timeout_seconds": CHECK_TIMEOUT_SECONDS}
    )
    text = str(output)
    return CheckResult(command, check_outcome(text, is_error), text)


async def changed_diff(parent: AgentRun, paths: Sequence[str]) -> str:
    """The diff of the given files in the project's repository; empty when there are none or it is no repository."""
    if not paths:
        return ""
    try:
        diff = await anyio.to_thread.run_sync(repository_diff, git_place_of(parent))
    except (GitError, TargetError, OSError):
        return ""
    return files_diff(diff, paths)


def sub_run_header(result: "SubRunResult") -> str:
    return f"Sub-run by {result.label} on {result.model}: {result.status}."


def escalation_note(step: EscalationStep, number: int, check: CheckResult, label: str, model: str) -> Note:
    """What happens after the failed attempt `number`: back to the worker with the failure, or to the main model."""
    if step == "worker_retry":
        text = "The check failed on the worker; the task goes back to it with the failure."
    else:
        text = "The check failed twice on the worker; the main model takes over the task."
    items = (f"check: {check.command}", f"attempt {number}: {label} on {model}, check failed")
    return Note("escalation", text, ESCALATION_WHY, items=items, count=number)


def log_check(
    delegator: "Delegator", result: "SubRunResult", check: CheckResult, number: int, by: Role, first_run_id: int | None
) -> None:
    """Keep the check's result in the transcript of the sub-run it judged."""
    if result.record_id is None:
        return
    entry: dict[str, Any] = {
        "type": "check",
        "command": check.command,
        "outcome": check.outcome,
        "output": capped(check.output, MAX_LOGGED_CHECK_OUTPUT),
        "attempt": number,
        "by": by,
        "first_run_id": first_run_id,
    }
    try:
        with delegator.session_factory() as session:
            record = session.get(AgentRunRecord, result.record_id)
            if record is None:
                return
            record.transcript = [*(record.transcript or []), entry]  # A new list, so the JSON change is seen.
            session.commit()
    except SQLAlchemyError:
        logger.warning("Storing the check result on the sub-run failed.", exc_info=True)


def attempts_line(history: Sequence[tuple[Role, str, AttemptOutcome]]) -> str:
    parts = [
        f"{number} {by} on {model} ({_OUTCOME_WORDS[outcome]})"
        for number, (by, model, outcome) in enumerate(history, 1)
    ]
    return f"Attempts: {', '.join(parts)}."


async def delegate_unchecked(
    delegator: "Delegator", parent: AgentRun, brief: Brief, agent_name: str | None, role: Role, worker: str | None
) -> str:
    """Briefed work without a check: one attempt, reported with the diff YAAP sees of the files it changed."""
    result = await delegator.run_sub_run(
        parent, brief_message(brief), agent_name, role, worker if role == "worker" else None, "work", "brief-worker"
    )
    diff = await changed_diff(parent, result.changed_files)
    body = lead_report(parse_report(result.full_report), result.full_report, None, diff, result.changed_files)
    return f"{sub_run_header(result)}\n\n{body}"


def attempt_message(
    step: NextAttempt, brief: Brief, last: "SubRunResult | None", check: CheckResult | None, diff: str
) -> str:
    """The message of the next attempt: the brief, then the brief with the failure, then the handover."""
    if last is None or check is None:
        return brief_message(brief)
    if step == "worker_retry":
        return retry_message(brief, check.output, last.full_report)
    return takeover_message(brief, last.full_report, diff, check.output)


async def delegate_by_brief(
    delegator: "Delegator", parent: AgentRun, brief: Brief, agent_name: str | None, first: Role, worker: str | None
) -> str:
    """Run the brief's attempts until the check passes or the escalation ends; the lead gets the last attempt."""
    command = brief.check
    if command is None:
        return await delegate_unchecked(delegator, parent, brief, agent_name, first, worker)
    attempts: list[Attempt] = []
    history: list[tuple[Role, str, AttemptOutcome]] = []
    changed: set[str] = set()
    first_run_id: int | None = None
    last: SubRunResult | None = None
    check: CheckResult | None = None
    step: NextAttempt = first
    while step != "stop":
        diff = ""
        if last is not None and check is not None and step in ("worker_retry", "main"):
            if step == "main":
                diff = await changed_diff(parent, sorted(changed))
            await record_note(parent, escalation_note(step, len(attempts), check, last.label, last.model))
        by: Role = "main" if step == "main" else "worker"
        last = await delegator.run_sub_run(
            parent,
            attempt_message(step, brief, last, check, diff),
            agent_name,
            by,
            worker if by == "worker" else None,
            "work",
            "brief-worker",
        )
        changed.update(last.changed_files)
        first_run_id = first_run_id if first_run_id is not None else last.record_id
        check = None
        outcome: AttemptOutcome = "stopped"
        if last.status != "incomplete":
            check = await run_check(delegator.runs, parent, command)
            outcome = check.outcome
            log_check(delegator, last, check, len(attempts) + 1, by, first_run_id)
        attempts.append(Attempt(by, outcome))
        history.append((by, last.model, outcome))
        step = next_attempt(attempts, first)
    if last is None:  # Not reached: the first step is never "stop".
        raise RuntimeError("No attempt was made.")
    diff = await changed_diff(parent, sorted(changed))
    body = lead_report(parse_report(last.full_report), last.full_report, check, diff, sorted(changed))
    return f"{sub_run_header(last)}\n\n{body}\n\n{attempts_line(history)}"
