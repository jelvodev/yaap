"""Proposals from a conversation's signals.

The review reads a project conversation's signals (app/learning/signals) and its messages, with the rules of every
Blueprint level, the project memory and the skills, and asks the utility model (task `learning`) for at most
MAX_PROPOSALS lasting changes at the right level: a rule in a standard of the global, environment or project level, a
line in the project memory, or a missing step in a skill. It also looks for corrections by the user, which are not
recorded live. Where *Remember as rule* and memory suggestions take one remark, the review looks for a pattern.

Without a utility model a deterministic fallback proposes what the signals say by themselves: a declined call with
the user's reason becomes a project rule, a test command that failed twice a memory line under *Lessons learned*.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.blueprint.builtin import render, shipped_instructions
from app.blueprint.layers import Item, LayerKind
from app.projects.memory import MAX_ENTRY_CHARS, SECTIONS
from app.storage.models import AgentRunRecord, RunStep
from app.tracing.recorder import SignalKind

ProposalKind = Literal["rule", "memory", "skill_step", "rule_removal"]
ProposalSource = Literal["utility", "fallback", "check"]
LEVELS: tuple[LayerKind, ...] = ("global", "environment", "project")
KINDS: tuple[ProposalKind, ...] = ("rule", "memory", "skill_step")
SIGNAL_KINDS: tuple[SignalKind, ...] = ("declined", "test_failed_twice", "known_search")
MAX_PROPOSALS = 3
MAX_TEXT_CHARS = 500
MAX_SIGNALS = 40
LESSONS = "Lessons learned"

# The answer format that parse_drafts() reads; the instructions before it may be replaced.
PROPOSALS_ANSWER = (
    "Answer with a JSON array only, no other text. Each item: "
    '{"kind": "rule" | "memory" | "skill_step", "level":\n"global" | "environment" | "project", '
    '"section": "...", "skill": "...", "text": "the rule, line or step, one\nsentence in {language}", '
    '"rationale": "why, in one or two sentences", "signals": [numbers of the signals it rests\non]}. '
    "Answer [] when nothing is worth changing."
)


@dataclass(frozen=True)
class StepSignal:
    """A signal as recorded on a step."""

    step_id: int
    kind: SignalKind
    tool: str
    summary: str
    note: str | None

    def as_json(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id, "signal": self.kind, "tool": self.tool, "summary": self.summary, "note": self.note
        }

    def line(self) -> str:
        what = {"declined": "declined by the user", "test_failed_twice": "test failed twice", "known_search": "known"}
        note = {"declined": "reason", "test_failed_twice": "command", "known_search": "note"}[self.kind]
        return f"{what[self.kind]}: {self.tool} ({self.summary})" + (f"; {note}: {self.note}" if self.note else "")


@dataclass(frozen=True)
class RuleView:
    """A rule of a standard as a level sees it: the level and file that define it."""

    level: LayerKind
    path: str
    rule: str


@dataclass(frozen=True)
class Draft:
    """A proposal before it is stored: what to change where, and why."""

    kind: ProposalKind
    level: LayerKind
    # A Blueprint path in the level's layer, or the memory section.
    target: str
    text: str
    rationale: str
    source: ProposalSource
    signals: tuple[StepSignal, ...] = ()


@dataclass(frozen=True)
class ReviewInput:
    project_id: int
    environment_id: int
    conversation_id: int
    signals: list[StepSignal]
    conversation: str
    rules: list[RuleView]
    memory: str
    skills: list[str]
    language: str
    pending: list[str] = field(default_factory=list)


def conversation_signals(session: Session, conversation_id: int, since: datetime | None) -> list[StepSignal]:
    """The signals on the steps of every run of the conversation, oldest first."""
    query = (
        select(RunStep)
        .join(AgentRunRecord, AgentRunRecord.id == RunStep.run_id)
        .where(AgentRunRecord.conversation_id == conversation_id, RunStep.signal.is_not(None))
    )
    if since is not None:
        query = query.where(RunStep.started_at > since)
    signals = []
    for step in session.scalars(query.order_by(RunStep.started_at, RunStep.id).limit(MAX_SIGNALS)):
        if step.signal in SIGNAL_KINDS:
            kind = cast(SignalKind, step.signal)
            signals.append(StepSignal(step.id, kind, step.tool or "", step.summary or "", step.signal_note))
    return signals


def has_signals(session: Session, conversation_id: int, since: datetime | None) -> bool:
    return bool(conversation_signals(session, conversation_id, since))


def rule_views(items: list[Item]) -> list[RuleView]:
    """The rules of the standards and engineering standards, by the level of the file that defines them."""
    return [
        RuleView(item.source.kind, item.path, rule)
        for item in items
        if item.kind in ("standard", "engineering")
        for rule in item.rules
    ]


def review_prompt(found: ReviewInput, instructions: str | None = None) -> str:
    """What the utility model is asked: the instructions, which the user may replace, then the answer format and the
    material."""
    signals = "\n".join(f"{number}. {signal.line()}" for number, signal in enumerate(found.signals, start=1))
    rules = "\n".join(f"- [{rule.level}] {rule.path}: {rule.rule}" for rule in found.rules)
    pending = "".join(f"\n- {text} (proposed, waiting)" for text in found.pending)
    template = instructions if instructions is not None else shipped_instructions("learner", "Proposals")
    lead = render(
        template,
        {"limit": str(MAX_PROPOSALS), "sections": ", ".join(SECTIONS), "skills": ", ".join(found.skills) or "(none)"},
    )
    asked = render(PROPOSALS_ANSWER, {"language": found.language})
    return (
        f"{lead}\n\n{asked}\n\n<signals>\n{signals or '(none)'}\n</signals>\n\n"
        f"<rules>\n{(rules + pending).strip() or '(none)'}\n</rules>\n\n"
        f"<memory>\n{found.memory.strip() or '(empty)'}\n</memory>\n\n"
        f"<conversation>\n{found.conversation or '(empty)'}\n</conversation>"
    )


def json_items(answer: str) -> list[Any]:
    start, end = answer.find("["), answer.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        items = json.loads(answer[start : end + 1])
    except json.JSONDecodeError:
        return []
    return items if isinstance(items, list) else []


def cited(item: dict[str, Any], signals: list[StepSignal]) -> tuple[StepSignal, ...]:
    numbers = item.get("signals")
    picked = []
    for number in numbers if isinstance(numbers, list) else []:
        if isinstance(number, int) and 1 <= number <= len(signals):
            picked.append(signals[number - 1])
    return tuple(dict.fromkeys(picked))


def draft_from(item: object, found: ReviewInput) -> Draft | None:
    """One answered item as a draft; None when it is malformed or points at nothing that exists."""
    if not isinstance(item, dict):
        return None
    kind, level = item.get("kind"), item.get("level") or "project"
    text = " ".join(str(item.get("text") or "").split())
    if kind not in KINDS or level not in LEVELS or not 10 <= len(text) <= MAX_TEXT_CHARS:
        return None
    rationale = " ".join(str(item.get("rationale") or "").split())[:1000]
    signals = cited(item, found.signals)
    if kind == "memory":
        section = str(item.get("section") or "Notes")
        target = section if section in SECTIONS else "Notes"
        return Draft("memory", "project", target, text[:MAX_ENTRY_CHARS], rationale, "utility", signals)
    if kind == "skill_step":
        skill = str(item.get("skill") or "")
        if skill not in found.skills:
            return None
        path = f"skills/{skill}/SKILL.md"
        return Draft("skill_step", cast(LayerKind, level), path, text, rationale, "utility", signals)
    return Draft("rule", cast(LayerKind, level), "", text, rationale, "utility", signals)


def parse_drafts(answer: str, found: ReviewInput) -> list[Draft]:
    drafts = [draft for draft in (draft_from(item, found) for item in json_items(answer)) if draft is not None]
    return drafts[:MAX_PROPOSALS]


def fallback_drafts(found: ReviewInput) -> list[Draft]:
    """What the signals say by themselves, for when no utility model answers."""
    drafts: list[Draft] = []
    for signal in found.signals:
        if signal.kind == "declined" and signal.note:
            rule = f"When calling {signal.tool}: {signal.note}"[:MAX_TEXT_CHARS]
            rationale = f"The user declined {signal.tool} ({signal.summary}) and said: {signal.note}"
            drafts.append(Draft("rule", "project", "", rule, rationale, "fallback", (signal,)))
        elif signal.kind == "test_failed_twice" and signal.note:
            line = f"The test command `{signal.note}` failed twice in one run; check what it needs before running it."
            rationale = "A test command failed twice in the same run."
            drafts.append(Draft("memory", "project", LESSONS, line[:MAX_ENTRY_CHARS], rationale, "fallback", (signal,)))
    unique: dict[tuple[str, str], Draft] = {}
    for draft in drafts:
        unique.setdefault((draft.kind, draft.text), draft)
    return list(unique.values())[:MAX_PROPOSALS]
