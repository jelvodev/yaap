"""What a build keeps about itself beside the plan's steps, stored under `body["build"]` of the plan row.

Pure values: how many uncommitted changes the project had (they are not in the build), who took each step's
attempts, the final run of every check on the finished tree (evidence for a review), and whether the branch reached
the project, at which commit.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from app.agent.escalation import AttemptOutcome, Role
from app.plans.model import MAX_NOTE, Plan, PlanStep, StepStatus

MAX_CHECK_OUTPUT = 2000
_ROLES: tuple[Role, ...] = ("worker", "main")
_OUTCOMES: tuple[AttemptOutcome, ...] = ("passed", "failed", "not_run", "stopped")


@dataclass(frozen=True)
class FinalCheck:
    command: str
    outcome: AttemptOutcome
    output: str


@dataclass(frozen=True)
class BuildInfo:
    uncommitted: int = 0
    # Per step number: who took each attempt, in order.
    attempts: dict[int, tuple[Role, ...]] = field(default_factory=dict)
    final_checks: tuple[FinalCheck, ...] = ()
    # The commit of the build branch brought into the project last, or None before it was.
    delivered: str | None = None


def build_info_to_json(info: BuildInfo) -> dict[str, Any]:
    return {
        "uncommitted": info.uncommitted,
        "attempts": {str(number): list(roles) for number, roles in info.attempts.items()},
        "final_checks": [
            {"command": check.command, "outcome": check.outcome, "output": check.output} for check in info.final_checks
        ],
        "delivered": info.delivered,
    }


def build_info_from_json(body: Mapping[str, Any]) -> BuildInfo:
    """The build's facts of a stored plan body; an empty BuildInfo when it has none yet."""
    value = body.get("build")
    if not isinstance(value, Mapping):
        return BuildInfo()
    attempts: dict[int, tuple[Role, ...]] = {}
    for number, roles in (value.get("attempts") or {}).items():
        if str(number).isdigit() and isinstance(roles, list):
            attempts[int(number)] = tuple(role for item in roles for role in _ROLES if role == item)
    checks = tuple(
        FinalCheck(
            str(check.get("command") or ""),
            next((outcome for outcome in _OUTCOMES if outcome == check.get("outcome")), "not_run"),
            str(check.get("output") or ""),
        )
        for check in value.get("final_checks") or []
        if isinstance(check, Mapping)
    )
    delivered = value.get("delivered")
    return BuildInfo(
        int(value.get("uncommitted") or 0), attempts, checks, delivered if isinstance(delivered, str) else None
    )


def with_step(plan: Plan, number: int, **changes: Any) -> Plan:
    """The plan with step `number` changed (status, note, commit, attempts)."""
    steps = tuple(replace(step, **changes) if step.number == number else step for step in plan.steps)
    return replace(plan, steps=steps)


def with_attempts(info: BuildInfo, number: int, roles: tuple[Role, ...]) -> BuildInfo:
    """The step's attempts after another round: the earlier ones (a build that continued) and these."""
    return replace(info, attempts={**info.attempts, number: (*info.attempts.get(number, ()), *roles)})


def open_steps(plan: Plan) -> tuple[PlanStep, ...]:
    """The steps a build still has to do, in order."""
    return tuple(step for step in plan.steps if step.status not in ("done", "skipped"))


def reopened(plan: Plan) -> Plan:
    """The plan as a continued build starts it: a step that was working or failed is open again."""
    again: tuple[StepStatus, ...] = ("working", "failed")
    return replace(
        plan, steps=tuple(replace(step, status="open") if step.status in again else step for step in plan.steps)
    )


def earlier_notes(plan: Plan, before: int) -> list[tuple[int, str]]:
    """The notes of the finished steps before step `before`, for the next brief."""
    return [(step.number, step.note) for step in plan.steps if step.number < before and step.status == "done"]


def capped_note(text: str) -> str:
    text = text.strip()
    return text if len(text) <= MAX_NOTE else text[: MAX_NOTE - 1].rstrip() + "…"


def capped_output(text: str) -> str:
    return text if len(text) <= MAX_CHECK_OUTPUT else text[:MAX_CHECK_OUTPUT] + "\n[cut]"
