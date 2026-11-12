"""The labels "behind the scenes" under one reply: the model chosen, memory, helpers, approvals, checks and the cache.

`behind_labels` is a pure function of what was kept for the reply (its route, its run tree, the steps and notes of
that tree), so the rules for each label are table-testable without a database. A reply made before notes were kept
shows no labels at all, because most labels would be missing and the rest would give a wrong impression.
"""

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Literal, get_args

from sqlalchemy.orm import Session

from app.core.why import Why, why_from
from app.storage.models import Message
from app.tracing.notes import CallWhy, NoteKind, NoteView, call_whys, clipped, run_notes
from app.tracing.steps import RunNode, StepView, reply_run_id, run_trace, steps_cost, walk

# Also the order the labels are shown in.
LabelKind = Literal[
    "route", "memory", "workers", "approvals", "guard", "secrets", "outgoing", "trim", "summary", "cache"
]
LABEL_KINDS: tuple[LabelKind, ...] = get_args(LabelKind)
WORKER_LINE_CHARS = 120


@dataclass(frozen=True)
class BehindLabel:
    kind: LabelKind
    # The short chip text.
    text: str
    # A sentence for the popover.
    detail: str | None = None
    why: Why | None = None
    cost: float | None = None
    tokens: int | None = None
    # Entries the label opens to: memory entries, helper tasks, secret NAMES (never values), approved tools.
    items: tuple[str, ...] = ()
    # Where the chat opens the trace for this label.
    run_id: int | None = None
    step_id: int | None = None


@dataclass(frozen=True)
class ReplyFacts:
    route: dict[str, Any] | None
    root: RunNode
    steps: dict[int, list[StepView]]
    notes: list[NoteView]
    calls: list[CallWhy] = field(default_factory=list)


@dataclass(frozen=True)
class ReplyBehind:
    labels: list[BehindLabel]
    calls: list[CallWhy]


def plural(count: int, singular: str, many: str) -> str:
    return f"{count} {singular if count == 1 else many}"


def notes_of(facts: ReplyFacts, kind: NoteKind) -> list[NoteView]:
    return [view for view in facts.notes if view.note.kind == kind]


def tree_steps(facts: ReplyFacts, root: RunNode | None = None) -> list[StepView]:
    """The steps of a run and all its sub-runs."""
    return [step for node in walk([root or facts.root]) for step in facts.steps.get(node.id, [])]


def from_note(kind: LabelKind, text: str, view: NoteView, detail: str | None = None) -> BehindLabel:
    note = view.note
    return BehindLabel(
        kind,
        text,
        detail if detail is not None else note.text,
        note.why,
        note.cost,
        None,
        note.items,
        view.run_id,
        view.step_id,
    )


def route_label(facts: ReplyFacts) -> BehindLabel | None:
    route = facts.route
    if not route:
        return None
    name = route.get("model_name") or route.get("model")
    if not isinstance(name, str) or not name:
        return None
    kind, role, reason = route.get("kind"), route.get("role"), route.get("reason")
    own = [step for step in facts.steps.get(facts.root.id, []) if step.kind == "model"]
    tokens = sum(step.usage.input_tokens + step.usage.output_tokens for step in own)
    return BehindLabel(
        "route",
        f"{name} · {kind}" if isinstance(kind, str) and kind else name,
        reason if isinstance(reason, str) and reason else None,
        why_from(route.get("why")),
        steps_cost(own),
        tokens if own else None,
        (f"role: {role}",) if isinstance(role, str) and role else (),
        facts.root.id,
        None,
    )


def memory_label(facts: ReplyFacts) -> BehindLabel | None:
    found = notes_of(facts, "memory")
    if not found:
        return None
    view = found[-1]
    count = view.note.count
    text = plural(count, "memory entry", "memory entries") if count is not None else view.note.text
    return from_note("memory", text, view)


def guard_label(facts: ReplyFacts) -> BehindLabel | None:
    found = notes_of(facts, "guard")
    if not found:
        return None
    flagged = [view for view in found if (view.note.count or 0) > 0 or "suspicious" in view.note.text.lower()]
    view = flagged[0] if flagged else found[-1]
    return from_note("guard", "guard: suspicious" if flagged else "guard: clean", view)


def secrets_label(facts: ReplyFacts) -> BehindLabel | None:
    found = notes_of(facts, "secrets_masked")
    if not found:
        return None
    names = tuple(dict.fromkeys(item for view in found for item in view.note.items))
    total = sum(view.note.count if view.note.count is not None else len(view.note.items) for view in found)
    first = found[0]
    return BehindLabel(
        "secrets",
        plural(total, "secret replaced", "secrets replaced"),
        "Secret values in tool output were replaced before the model saw them.",
        first.note.why,
        None,
        None,
        names,
        first.run_id,
        first.step_id,
    )


def worker_line(node: RunNode) -> str:
    name = node.worker or node.agent or "helper"
    return clipped(f"{name}: {node.task}" if node.task else name, WORKER_LINE_CHARS)


def workers_label(facts: ReplyFacts) -> BehindLabel | None:
    children = facts.root.children
    if not children:
        return None
    return BehindLabel(
        "workers",
        plural(len(children), "helper", "helpers"),
        "Helpers took parts of the work; their model calls are counted with this reply.",
        None,
        steps_cost(step for child in children for step in tree_steps(facts, child)),
        None,
        tuple(worker_line(child) for child in children),
        children[0].id,
        None,
    )


def approvals_label(facts: ReplyFacts) -> BehindLabel | None:
    asked = [step for step in tree_steps(facts) if step.kind == "tool" and step.approval not in (None, "case_approved")]
    if not asked:
        return None
    outcomes = Counter(step.approval for step in asked)
    if len(outcomes) == 1:
        (outcome,) = outcomes
        text = plural(len(asked), "approval", "approvals") if outcome == "approved" else f"{len(asked)} {outcome}"
    else:
        text = plural(len(asked), "approval", "approvals")
    ids = {step.id for step in asked}
    why = next((call.why for call in facts.calls if call.step_id in ids), None)
    return BehindLabel(
        "approvals",
        text,
        "Tool calls that needed a decision: " + ", ".join(f"{count} {outcome}" for outcome, count in outcomes.items()),
        why,
        None,
        None,
        tuple(f"{step.tool or 'tool'}: {step.approval}" for step in asked),
        asked[0].run_id,
        asked[0].id,
    )


def cache_label(facts: ReplyFacts) -> BehindLabel | None:
    found = notes_of(facts, "cache")
    if not found:
        return None
    usages = [step.usage for step in tree_steps(facts) if step.kind == "model"]
    prompt = sum(u.input_tokens + u.cache_read_tokens + u.cache_write_tokens for u in usages)
    view = found[-1]
    if prompt <= 0:
        return from_note("cache", "cache", view)
    rate = sum(u.cache_read_tokens for u in usages) / prompt
    return from_note("cache", f"cache {round(rate * 100)}%", view)


def single_note_label(facts: ReplyFacts, kind: NoteKind, label: LabelKind, text: str) -> BehindLabel | None:
    found = notes_of(facts, kind)
    return from_note(label, text, found[-1]) if found else None


def behind_labels(facts: ReplyFacts) -> list[BehindLabel]:
    """The labels for one reply in display order; none when no note was kept for it."""
    if not facts.notes:
        return []
    made: dict[LabelKind, BehindLabel | None] = {
        "route": route_label(facts),
        "memory": memory_label(facts),
        "workers": workers_label(facts),
        "approvals": approvals_label(facts),
        "guard": guard_label(facts),
        "secrets": secrets_label(facts),
        "outgoing": single_note_label(facts, "outgoing_blocked", "outgoing", "output blocked"),
        "trim": single_note_label(facts, "trim", "trim", "older output shortened"),
        "summary": single_note_label(facts, "summary", "summary", "summarised"),
        "cache": cache_label(facts),
    }
    return [label for kind in LABEL_KINDS if (label := made[kind]) is not None]


def reply_facts(session: Session, conversation_id: int, message_id: int) -> ReplyFacts | None:
    """What was kept for a reply; None when the reply has no run."""
    run_id = reply_run_id(session, conversation_id, message_id)
    trace = run_trace(session, conversation_id, run_id) if run_id is not None else None
    if trace is None:
        return None
    message = session.get(Message, message_id)
    route = message.route if message is not None and message.conversation_id == conversation_id else None
    run_ids = [node.id for node in walk([trace.root])]
    return ReplyFacts(route, trace.root, trace.steps, run_notes(session, run_ids), call_whys(session, run_ids))


def reply_behind(session: Session, conversation_id: int, message_id: int) -> ReplyBehind | None:
    facts = reply_facts(session, conversation_id, message_id)
    if facts is None:
        return None
    return ReplyBehind(behind_labels(facts), facts.calls)


__all__ = [
    "BehindLabel",
    "LabelKind",
    "ReplyBehind",
    "ReplyFacts",
    "behind_labels",
    "reply_behind",
    "reply_facts",
]

