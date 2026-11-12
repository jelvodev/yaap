"""A plan as Markdown, for exports and the card file. Pure: the same plan gives the same text."""

import re

from app.plans.model import Plan, PlanStep, StepCheck

STATUS_MARKS = {"open": " ", "working": " ", "done": "x", "failed": " ", "skipped": "x"}


def code_span(text: str) -> str:
    """Inline code longer than any backtick run in the text, so the text cannot close it."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    marks = "`" * (longest + 1)
    padding = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{marks}{padding}{text}{padding}{marks}"


def check_line(check: StepCheck) -> str:
    if check.run:
        return f"  - {check.text} ({code_span('run: ' + check.run)})"
    return f"  - {check.text}"


def step_markdown(step: PlanStep) -> str:
    state = "" if step.status in ("open", "done") else f" *({step.status})*"
    lines = [f"- [{STATUS_MARKS.get(step.status, ' ')}] **{step.number}. {step.title}**{state}"]
    if step.details:
        lines += ["", *(f"  {line}" for line in step.details.splitlines())]
    if step.files:
        lines += ["", "  Files: " + ", ".join(code_span(path) for path in step.files)]
    if step.checks:
        lines += ["", "  Checks:", *(f"  {check_line(check)}" for check in step.checks)]
    if step.note:
        lines += ["", f"  Note: {step.note}"]
    return "\n".join(lines)


def plan_markdown(plan: Plan) -> str:
    """The plan's title, summary, steps with their files and checks, and risks."""
    parts = [f"### Plan: {plan.title}"]
    if plan.summary:
        parts.append(plan.summary)
    parts.append("\n\n".join(step_markdown(step) for step in plan.steps))
    if plan.risks:
        parts.append("**Risks**\n\n" + "\n".join(f"- {risk}" for risk in plan.risks))
    return "\n\n".join(parts)


def plan_outline(plan: Plan) -> str:
    """The plan in a few lines: its title and step titles, as the model's history shows a plan part."""
    steps = "\n".join(f"{step.number}. {step.title}" for step in plan.steps)
    return f"Plan: {plan.title}\n{steps}"
