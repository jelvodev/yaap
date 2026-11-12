"""A plan as plain values: its steps, the checks that prove each step, its status and how that status moves.

Everything here is pure. `validate_plan` turns what a model or a hand edit sends into a `Plan`, or into every error at
once, each naming the step it is about, so the model can fix them in one go. `next_status` is the plan's state
machine: any pair it does not know returns None, and the caller refuses the change.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, get_args

from app.agent.brief import MAX_CHECK

PlanStatus = Literal["draft", "approved", "queued", "building", "interrupted", "done", "failed", "cancelled"]
StepStatus = Literal["open", "working", "done", "failed", "skipped"]
PlanEvent = Literal[
    "submit",
    "edit",
    "approve",
    "queue",
    "build_started",
    "all_steps_done",
    "step_failed",
    "stop",
    "restart_lost",
    "continue",
    "discard",
    "replan",
]

MAX_STEPS = 30
MAX_CHECKS = 10
MAX_FILES = 40
MAX_RISKS = 20
MAX_TITLE = 200
MAX_STEP_TITLE = 200
MAX_SUMMARY = 2000
MAX_DETAILS = 4000
MAX_CHECK_TEXT = 300
MAX_RISK = 1000
MAX_NOTE = 2000
MAX_PATH = 300
MAX_BODY = 60_000

NO_RUN_WARNING = "No step has a command YAAP can run; the build will ask the main model to verify instead."

_DRIVE = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True)
class StepCheck:
    # How a person knows the step is done.
    text: str
    # One command line YAAP runs to prove it, or None when only a person or the model can tell.
    run: str | None = None


@dataclass(frozen=True)
class PlanStep:
    number: int
    title: str
    details: str = ""
    # Paths relative to the project, as advice: YAAP reads nothing by them.
    files: tuple[str, ...] = ()
    checks: tuple[StepCheck, ...] = ()
    status: StepStatus = "open"
    note: str = ""
    commit: str | None = None
    attempts: int = 0


@dataclass(frozen=True)
class Plan:
    title: str
    summary: str = ""
    risks: tuple[str, ...] = ()
    steps: tuple[PlanStep, ...] = ()


# (from, event) -> to; `None` as from is a plan that does not exist yet.
TRANSITIONS: dict[tuple[PlanStatus | None, PlanEvent], PlanStatus] = {
    (None, "submit"): "draft",
    ("draft", "edit"): "draft",
    ("draft", "approve"): "approved",
    ("approved", "build_started"): "building",
    ("approved", "queue"): "queued",
    ("queued", "build_started"): "building",
    ("building", "all_steps_done"): "done",
    ("building", "step_failed"): "failed",
    ("building", "stop"): "failed",
    ("building", "restart_lost"): "interrupted",
    ("interrupted", "continue"): "building",
    ("failed", "continue"): "building",
    **{
        (status, event): "cancelled"
        for status in ("draft", "approved", "queued", "interrupted", "failed")
        for event in ("discard", "replan")
    },
}


def next_status(current: PlanStatus | None, event: PlanEvent) -> PlanStatus | None:
    """The status after `event`, or None when the event does not apply to a plan in `current`."""
    return TRANSITIONS.get((current, event))


def plan_progress(plan: Plan) -> tuple[int, int]:
    """How many steps are done (or skipped) out of all steps."""
    finished = sum(1 for step in plan.steps if step.status in ("done", "skipped"))
    return finished, len(plan.steps)


def plan_warnings(plan: Plan) -> list[str]:
    """What is accepted but worth saying: a plan without any check YAAP can run."""
    if any(check.run for step in plan.steps for check in step.checks):
        return []
    return [NO_RUN_WARNING]


def path_problem(path: str) -> str | None:
    """Why a file path of a step is not usable, or None. Paths are relative, with `/`, inside the project."""
    if not path.strip():
        return "a file path is empty"
    if "\0" in path:
        return "a file path has a NUL character"
    if len(path) > MAX_PATH:
        return f"a file path is over {MAX_PATH} characters"
    if "\\" in path:
        return f"{path} uses \\; write paths with /"
    if path.startswith("/") or _DRIVE.match(path):
        return f"{path} is absolute; write it relative to the project"
    if ".." in path.split("/"):
        return f"{path} goes outside the project (..)"
    return None


def text_value(value: object, name: str, limit: int, errors: list[str], required: bool = False) -> str:
    """A text field: a string within its limit; a problem is added to `errors` and an empty string returned."""
    if value is None:
        value = ""
    if not isinstance(value, str):
        errors.append(f"{name} must be text.")
        return ""
    text = value.strip()
    if required and not text:
        errors.append(f"{name} is empty.")
    elif len(text) > limit:
        errors.append(f"{name} is over {limit} characters.")
    return text


def text_list(value: object, name: str, errors: list[str]) -> list[object]:
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append(f"{name} must be a list.")
        return []
    return value


def parse_check(value: object, where: str, errors: list[str]) -> StepCheck:
    if isinstance(value, str):
        value = {"text": value}
    if not isinstance(value, Mapping):
        errors.append(f"{where} must have a text and optionally a run command.")
        return StepCheck("")
    text = text_value(value.get("text"), f"{where} text", MAX_CHECK_TEXT, errors, required=True)
    run = value.get("run")
    if run is None or (isinstance(run, str) and not run.strip()):
        return StepCheck(text)
    if not isinstance(run, str):
        errors.append(f"{where} run must be a command line.")
        return StepCheck(text)
    command = run.strip()
    if "\n" in command or "\r" in command or "\0" in command:
        errors.append(f"{where} run must be one command line.")
    elif len(command) > MAX_CHECK:
        errors.append(f"{where} run is over {MAX_CHECK} characters.")
    return StepCheck(text, command)


def parse_step(value: object, number: int, errors: list[str]) -> PlanStep:
    where = f"Step {number}"
    if not isinstance(value, Mapping):
        errors.append(f"{where} must have a title, details, files and checks.")
        return PlanStep(number, "")
    title = text_value(value.get("title"), f"{where} title", MAX_STEP_TITLE, errors, required=True)
    details = text_value(value.get("details"), f"{where} details", MAX_DETAILS, errors)
    files: list[str] = []
    for path in text_list(value.get("files"), f"{where} files", errors)[: MAX_FILES + 1]:
        problem = path_problem(path) if isinstance(path, str) else "a file path is not text"
        if problem is not None:
            errors.append(f"{where}: {problem}.")
        else:
            files.append(str(path).strip())
    if len(files) > MAX_FILES:
        errors.append(f"{where} lists more than {MAX_FILES} files.")
    raw_checks = text_list(value.get("checks"), f"{where} checks", errors)
    if not raw_checks:
        errors.append(f"{where} has no checks: say how to know it is done.")
    elif len(raw_checks) > MAX_CHECKS:
        errors.append(f"{where} has more than {MAX_CHECKS} checks.")
    checks = tuple(
        parse_check(check, f"{where} check {index}", errors) for index, check in enumerate(raw_checks[:MAX_CHECKS], 1)
    )
    note = text_value(value.get("note"), f"{where} note", MAX_NOTE, errors)
    return PlanStep(number, title, details, tuple(files), checks, note=note)


def validate_plan(arguments: Mapping[str, Any]) -> Plan | list[str]:
    """The plan in `arguments` (title, summary, risks, steps), or every reason it cannot be used."""
    errors: list[str] = []
    title = text_value(arguments.get("title"), "The title", MAX_TITLE, errors, required=True)
    summary = text_value(arguments.get("summary"), "The summary", MAX_SUMMARY, errors)
    risks = [
        text_value(risk, f"Risk {index}", MAX_RISK, errors, required=True)
        for index, risk in enumerate(text_list(arguments.get("risks"), "The risks", errors)[:MAX_RISKS], 1)
    ]
    raw_steps = text_list(arguments.get("steps"), "The steps", errors)
    if not raw_steps:
        errors.append("The plan has no steps: give at least one.")
    elif len(raw_steps) > MAX_STEPS:
        errors.append(f"The plan has {len(raw_steps)} steps; at most {MAX_STEPS}.")
    steps = tuple(parse_step(step, number, errors) for number, step in enumerate(raw_steps[:MAX_STEPS], 1))
    plan = Plan(title, summary, tuple(risks), steps)
    if not errors and len(plan_json_text(plan)) > MAX_BODY:
        errors.append(f"The plan is over {MAX_BODY} characters; make it shorter.")
    return errors or plan


def check_to_json(check: StepCheck) -> dict[str, Any]:
    return {"text": check.text, "run": check.run}


def step_to_json(step: PlanStep) -> dict[str, Any]:
    return {
        "number": step.number,
        "title": step.title,
        "details": step.details,
        "files": list(step.files),
        "checks": [check_to_json(check) for check in step.checks],
        "status": step.status,
        "note": step.note,
        "commit": step.commit,
        "attempts": step.attempts,
    }


def plan_to_json(plan: Plan) -> dict[str, Any]:
    return {
        "title": plan.title,
        "summary": plan.summary,
        "risks": list(plan.risks),
        "steps": [step_to_json(step) for step in plan.steps],
    }


def plan_json_text(plan: Plan) -> str:
    """The plan's stored text, for its size limit."""
    return json.dumps(plan_to_json(plan), ensure_ascii=False)


STEP_STATUSES: tuple[StepStatus, ...] = get_args(StepStatus)


def step_status(value: object) -> StepStatus:
    return next((status for status in STEP_STATUSES if status == value), "open")


def step_from_json(value: Mapping[str, Any]) -> PlanStep:
    checks = tuple(
        StepCheck(str(check.get("text") or ""), str(check["run"]) if check.get("run") else None)
        for check in value.get("checks") or []
    )
    commit = value.get("commit")
    return PlanStep(
        number=int(value.get("number") or 0),
        title=str(value.get("title") or ""),
        details=str(value.get("details") or ""),
        files=tuple(str(path) for path in value.get("files") or []),
        checks=checks,
        status=step_status(value.get("status")),
        note=str(value.get("note") or ""),
        commit=str(commit) if commit else None,
        attempts=int(value.get("attempts") or 0),
    )


def plan_from_json(value: Mapping[str, Any]) -> Plan:
    """A stored plan; it was validated when it was stored, so this only reads it back."""
    return Plan(
        title=str(value.get("title") or ""),
        summary=str(value.get("summary") or ""),
        risks=tuple(str(risk) for risk in value.get("risks") or []),
        steps=tuple(step_from_json(step) for step in value.get("steps") or []),
    )

