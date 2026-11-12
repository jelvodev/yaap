"""YAAP's own run of a card's acceptance criteria on the build's copy, before the review.

A criterion with a `run:` or `file:` check (specs/criteria.py) is checked on the copy the build worked in, never in
YAAP's process: the checks get a CheckContext made from the files and the command executor of a run opened on the copy
exactly as the build's own run is (plans/build_run.open_build_run), so in the runner or the sandbox when the project
works there. A command runs only when approving the plan approved it (`may_run`): the plan's check commands and the
card's criteria commands shown with the plan. A criterion command added or changed after the approval is not run; its
criterion stays unchecked and says why. Nothing else is allowed, also not the user's own command allow list.
"""

import logging
from collections.abc import Sequence

import anyio

from app.agent.policy import build_command_allowed
from app.agent.runs import AgentRun
from app.agent.tool_context import executor, files
from app.agent.toolkit import ToolError
from app.core.reply_services import ReplyServices
from app.evals.checks import CheckContext, command_result, file_result
from app.evals.models import CheckResult, CommandCheck, FileCheck
from app.plans.build_run import close_build_run, open_build_run, plan_commands
from app.plans.service import BuildRefused, PlanView
from app.reviews.evidence import CheckOutcome, CriterionCheck
from app.specs.criteria import Criterion, parse_criteria
from app.storage.models import AgentRunRecord

logger = logging.getLogger(__name__)

NOT_APPROVED = "Not run: approving the plan did not approve this command (the criterion changed after it)."
COPY_GONE = "Not run: the build's copy is gone."


def card_criteria(specification: str) -> list[Criterion]:
    """The criteria of a card's specification; none when they cannot be read (the card shows why elsewhere)."""
    parsed = parse_criteria(specification)
    return [item for item in parsed if isinstance(item, Criterion)]


def criteria_commands(criteria: Sequence[Criterion]) -> tuple[str, ...]:
    """The commands of the criteria with a `run:` check, once each, in order: what the plan card shows beside the
    plan's own commands, and what approving the plan approves with them."""
    return tuple(dict.fromkeys(item.check.run for item in criteria if isinstance(item.check, CommandCheck)))


def approved_commands(view: PlanView) -> tuple[str, ...]:
    """What the user approved with the plan: its check commands and the card's criteria commands shown with it."""
    return tuple(dict.fromkeys((*plan_commands(view.plan), *view.build.criteria_commands)))


def may_run(command: str, approved: tuple[str, ...]) -> bool:
    """The same rule as the plan's checks in its build: exactly an approved command, nothing else."""
    return build_command_allowed({"command": command}, approved)


def checkable(criteria: Sequence[Criterion]) -> list[Criterion]:
    return [item for item in criteria if isinstance(item.check, CommandCheck | FileCheck)]


def outcome_of(result: CheckResult) -> CheckOutcome:
    if result.status == "passed":
        return "passed"
    return "failed" if result.status == "failed" else "error"


async def check_criteria(
    criteria: Sequence[Criterion], context: CheckContext, approved: tuple[str, ...]
) -> list[CriterionCheck]:
    """Each command and file criterion checked through the context; an unapproved command is not run."""
    results: list[CriterionCheck] = []
    for criterion in checkable(criteria):
        check = criterion.check
        if isinstance(check, CommandCheck):
            if not may_run(check.run, approved):
                results.append(CriterionCheck(criterion.number, "command", check.run, "not_run", NOT_APPROVED))
                continue
            result = await command_result(check, context)
            results.append(CriterionCheck(criterion.number, "command", check.run, outcome_of(result), result.detail))
        elif isinstance(check, FileCheck):
            result = await file_result(check, context.files)
            label = f"file: {check.path}"
            results.append(CriterionCheck(criterion.number, "file", label, outcome_of(result), result.detail))
    return results


def run_context(run: AgentRun) -> CheckContext:
    """The checks' context of a run: its files and its executor, as its tools get them."""
    return CheckContext(files(run), executor(run))


def not_run(criteria: Sequence[Criterion], reason: str) -> list[CriterionCheck]:
    """Every command and file criterion as not run, with the reason."""
    results: list[CriterionCheck] = []
    for item in checkable(criteria):
        check = item.check
        if isinstance(check, CommandCheck):
            results.append(CriterionCheck(item.number, "command", check.run, "not_run", reason))
        elif isinstance(check, FileCheck):
            results.append(CriterionCheck(item.number, "file", f"file: {check.path}", "not_run", reason))
    return results


async def check_on_copy(services: ReplyServices, view: PlanView, criteria: Sequence[Criterion]) -> list[CriterionCheck]:
    """Check the criteria on the plan's build copy, in a run opened there like the build's. On the event loop."""
    if not checkable(criteria):
        return []
    try:
        build = await anyio.to_thread.run_sync(open_build_run, services, view)
    except BuildRefused as error:
        logger.info("The criteria of plan %s were not checked: %s", view.id, error)
        return not_run(criteria, f"Not run: {error}")
    if not (build.copy_folder / ".git").is_dir():
        close_build_run(services, build, "incomplete")
        return not_run(criteria, COPY_GONE)
    approved = approved_commands(view)
    await anyio.to_thread.run_sync(name_run, services, build.run, view.id)
    try:
        context = run_context(build.run)
    except ToolError as error:
        close_build_run(services, build, "incomplete")
        return not_run(criteria, f"Not run: {error}")
    try:
        return await check_criteria(criteria, context, approved)
    finally:
        close_build_run(services, build, "complete")


def name_run(services: ReplyServices, run: AgentRun, plan_id: int) -> None:
    if run.state.record_id is None:
        return
    with services.session_factory() as session:
        record = session.get(AgentRunRecord, run.state.record_id)
        if record is not None:
            record.task = f"criteria checks of plan {plan_id}"
            session.commit()
