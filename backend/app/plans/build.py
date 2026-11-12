"""What a build makes of a plan, as plain values: the brief per step, the build branch's name, the commit message.

Everything here is pure. A step becomes a brief for the worker (agent/brief.py): its title and details are the task,
the plan's summary, the card's acceptance criteria and the notes of the steps done so far are the facts, the checks'
texts say when it is done, and the checks YAAP can run become the brief's one check line. Checks that do not fit that
line are run by YAAP after the attempts (`remaining_checks`).
"""

import re
from collections.abc import Sequence

from app.agent.brief import MAX_CHECK, MAX_DONE_WHEN, MAX_FACTS, MAX_FILES, Brief, FileRef
from app.plans.model import Plan, PlanStep

MAX_SLUG = 30
STEP_NOTE_LIMIT = 600
_NOT_SLUG = re.compile(r"[^a-z0-9]+")


def run_checks(step: PlanStep) -> tuple[str, ...]:
    """The commands of the step's checks that YAAP can run, in order."""
    return tuple(check.run for check in step.checks if check.run)


def brief_check(step: PlanStep) -> str | None:
    """The brief's check: all run commands joined with `&&` when they fit MAX_CHECK, else the first one."""
    commands = run_checks(step)
    if not commands:
        return None
    joined = " && ".join(commands)
    return joined if len(joined) <= MAX_CHECK else commands[0]


def remaining_checks(step: PlanStep) -> tuple[str, ...]:
    """The run commands the brief's check does not cover, which YAAP runs after the attempts."""
    commands = run_checks(step)
    check = brief_check(step)
    if check is None or check != commands[0] or len(commands) == 1:
        return ()
    return commands[1:]


def capped(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip() + "…"


def step_facts(plan: Plan, earlier_notes: Sequence[tuple[int, str]], criteria: str = "") -> str:
    """The plan's summary, the card's acceptance criteria and what earlier steps reported, within MAX_FACTS. The
    newest notes are kept first when they do not all fit."""
    parts: list[str] = []
    if plan.summary:
        parts.append(f"The plan: {plan.title}\n{plan.summary}")
    else:
        parts.append(f"The plan: {plan.title}")
    if criteria.strip():
        parts.append(f"Acceptance criteria of the card:\n{criteria.strip()}")
    head = "\n\n".join(parts)
    head = capped(head, MAX_FACTS)
    notes: list[str] = []
    room = MAX_FACTS - len(head) - len("\n\nDone so far:")
    for number, note in reversed(earlier_notes):
        line = f"\n- Step {number}: {capped(' '.join(note.split()), STEP_NOTE_LIMIT)}"
        if len(line) > room:
            break
        notes.insert(0, line)
        room -= len(line)
    if notes:
        return head + "\n\nDone so far:" + "".join(notes)
    return head


def step_brief(plan: Plan, step: PlanStep, earlier_notes: Sequence[tuple[int, str]], criteria: str = "") -> Brief:
    """The brief for one step of the plan."""
    task = step.title if not step.details else f"{step.title}\n\n{step.details}"
    done_when = capped("\n".join(f"- {check.text}" for check in step.checks), MAX_DONE_WHEN)
    return Brief(
        task=task,
        files=tuple(FileRef(path) for path in step.files[:MAX_FILES]),
        facts=step_facts(plan, earlier_notes, criteria),
        done_when=done_when,
        check=brief_check(step),
    )


def slug(title: str) -> str:
    """Lower-case letters, digits and single dashes, at most MAX_SLUG characters; "plan" when nothing is left."""
    text = _NOT_SLUG.sub("-", title.lower()).strip("-")[:MAX_SLUG].strip("-")
    return text or "plan"


def build_branch_name(plan_id: int, card_id: int | None, title: str) -> str:
    """`yaap/<card id>-<slug>` for a card's plan, else `yaap/plan-<plan id>-<slug>`."""
    prefix = f"{card_id}" if card_id is not None else f"plan-{plan_id}"
    return f"yaap/{prefix}-{slug(title)}"


def numbered_branch(name: str, taken: Sequence[str]) -> str:
    """The name, or the name with `-2`, `-3`, … when a branch of that name exists."""
    if name not in taken:
        return name
    number = 2
    while f"{name}-{number}" in taken:
        number += 1
    return f"{name}-{number}"


def step_commit_message(step: PlanStep) -> str:
    """The commit of a finished step: "Step N: <title>" on one line."""
    title = " ".join(step.title.split())
    return capped(f"Step {step.number}: {title}", 200)
