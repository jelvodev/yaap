"""The stops of a route other than the main line: stacked tool calls, a helper's line, work after the reply, notes."""

from collections import Counter
from dataclasses import dataclass, field

from app.core.why import why_out
from app.providers.base import TokenUsage
from app.tracing.notes import NOTE_ITEM_CHARS, NOTE_MAX_ITEMS, ApprovalOutcome, Note, clipped
from app.tracing.route_facts import RouteStep, UtilityUse, WaitingCall, WorkerRun
from app.tracing.route_schema import (
    RouteCheckKind,
    RouteCheckOut,
    RouteLane,
    RouteState,
    RouteStopOut,
    RouteTokensOut,
)

AFTERWARDS_LABELS = {
    "title": "Title",
    "title_and_memory": "Title and memory",
    "memory_suggestion": "Memory suggestions",
    "memory_tidy": "Memory tidying",
    "summary": "Summary",
    "learning": "Learning proposals",
    "trim": "Shorter old output",
}
# Utility calls that happen before the reply, not after it; they have their own stops.
BEFORE_REPLY_TASKS = frozenset({"route", "injection_check"})
FAILED_RUN_STATUSES = frozenset({"error", "failed"})
APPROVAL_TEXT: dict[ApprovalOutcome, tuple[RouteState, str]] = {
    "approved": ("ran", "You approved the call."),
    "declined": ("failed", "You declined the call."),
    "refused": ("failed", "The permission rules refused the call."),
    "asked": ("skipped", "The call needed approval and nobody was there to give it."),
    "case_approved": ("ran", "Allowed by the eval case's approval."),
}
APPROVAL_REASON: dict[ApprovalOutcome, str] = {
    "declined": "Declined",
    "refused": "Refused by the permission rules",
    "asked": "Nobody to approve",
}


def humanised(text: str) -> str:
    spaced = text.replace("_", " ").strip()
    return spaced[:1].upper() + spaced[1:]


def tokens_of(usage: TokenUsage) -> RouteTokensOut:
    return RouteTokensOut(
        new=usage.input_tokens,
        cache_read=usage.cache_read_tokens,
        cache_write=usage.cache_write_tokens,
        output=usage.output_tokens,
    )


def summed_tokens(steps: list[RouteStep]) -> RouteTokensOut | None:
    models = [step.usage for step in steps if step.kind == "model"]
    if not models:
        return None
    return RouteTokensOut(
        new=sum(u.input_tokens for u in models),
        cache_read=sum(u.cache_read_tokens for u in models),
        cache_write=sum(u.cache_write_tokens for u in models),
        output=sum(u.output_tokens for u in models),
    )


def summed_cost(steps: list[RouteStep]) -> float | None:
    costs = [step.cost for step in steps if step.cost is not None]
    return sum(costs) if costs else None


def unique(checks: list[RouteCheckOut]) -> list[RouteCheckOut]:
    seen: set[tuple[str, str, str]] = set()
    kept: list[RouteCheckOut] = []
    for check in checks:
        key = (check.kind, check.state, check.text)
        if key not in seen:
            seen.add(key)
            kept.append(check)
    return kept


def clipped_items(items: list[str]) -> list[str]:
    return [clipped(item, NOTE_ITEM_CHARS) for item in items[:NOTE_MAX_ITEMS]]


def tool_checks(step: RouteStep) -> list[RouteCheckOut]:
    """What the gate did with one call: the rule behind the decision, the decision itself, a long output stored."""
    state: RouteState = APPROVAL_TEXT[step.approval][0] if step.approval else "ran"
    checks: list[RouteCheckOut] = []
    if step.why is not None:
        checks.append(RouteCheckOut(kind="permission", state=state, text=step.why.rule, why=why_out(step.why)))
    if step.approval is not None:
        checks.append(RouteCheckOut(kind="approval", state=state, text=APPROVAL_TEXT[step.approval][1]))
    if step.output_ref is not None:
        checks.append(RouteCheckOut(kind="output_stored", state="ran", text="The long output was stored in full."))
    return checks


@dataclass
class ToolStack:
    """Consecutive calls of one tool after one model call, drawn as one stop."""

    tool: str
    parent: str
    steps: list[RouteStep]
    checks: list[RouteCheckOut] = field(default_factory=list)

    def stop(self) -> RouteStopOut:
        first = self.steps[0]
        failed = next((s for s in self.steps if s.is_error or s.approval in ("declined", "refused")), None)
        skipped = next((s for s in self.steps if s.approval == "asked"), None)
        state: RouteState = "failed" if failed else "skipped" if skipped else "ran"
        reason: str | None = None
        if failed is not None:
            reason = APPROVAL_REASON.get(failed.approval) if failed.approval else None
            reason = reason or clipped(failed.summary or "The call failed.", NOTE_ITEM_CHARS)
        elif skipped is not None:
            reason = APPROVAL_REASON["asked"]
        summaries = [step.summary or self.tool for step in self.steps]
        checks = [check for step in self.steps for check in tool_checks(step)] + self.checks
        return RouteStopOut(
            id=f"tool:{first.run_id}:{first.position}",
            stage="tool",
            lane="tools",
            parent=self.parent,
            label=self.tool,
            count=len(self.steps),
            state=state,
            reason=reason,
            detail=clipped(summaries[0], NOTE_ITEM_CHARS) if len(self.steps) == 1 else f"{len(self.steps)} calls",
            started_at=first.started_at,
            duration_ms=sum(step.duration_ms for step in self.steps),
            checks=unique(checks),
            items=clipped_items(summaries),
            run_id=first.run_id,
            step_id=first.step_id,
        )


def waiting_stop(call: WaitingCall, parent: str, position: int) -> RouteStopOut:
    return RouteStopOut(
        id=f"tool:{call.run_id}:{position}",
        stage="tool",
        lane="tools",
        parent=parent,
        label=call.tool,
        state="waiting",
        detail=clipped(call.summary, NOTE_ITEM_CHARS) if call.summary else None,
        started_at=call.since,
        checks=[RouteCheckOut(kind="approval", state="waiting", text="Waiting for your decision.")],
        items=clipped_items([call.summary] if call.summary else []),
        run_id=call.run_id,
    )


def note_check(note: Note, kind: RouteCheckKind) -> RouteCheckOut:
    return RouteCheckOut(kind=kind, state="ran", text=note.text, why=why_out(note.why) if note.why else None)


def generic_note_stop(
    step: RouteStep, lane: RouteLane, parent: str | None, line: str | None, label: str | None = None
) -> RouteStopOut:
    """A note shown as a stop of its own: a kind the builder's table gives a label, or a kind it has no place for, so
    nothing is lost."""
    note = step.note
    return RouteStopOut(
        id=f"note:{step.run_id}:{step.position}",
        stage="note",
        lane=lane,
        line=line,
        parent=parent,
        label=label or (humanised(note.kind) if note else "Note"),
        detail=note.text if note else None,
        started_at=step.started_at,
        why=why_out(note.why) if note and note.why else None,
        items=clipped_items(list(note.items)) if note else [],
        run_id=step.run_id,
        step_id=step.step_id,
    )


def tool_items(steps: list[RouteStep]) -> list[str]:
    counts = Counter(step.tool or "tool" for step in steps if step.kind == "tool")
    return [f"{tool} ×{count}" if count > 1 else tool for tool, count in counts.items()]


def worker_stop(
    worker: WorkerRun, steps: list[RouteStep], parent: str, checks: list[RouteCheckOut], waiting: bool
) -> RouteStopOut:
    """One helper's whole line as a single stop: its model and tool calls counted, not drawn one by one."""
    failed = worker.status in FAILED_RUN_STATUSES or any(step.is_error for step in steps)
    state: RouteState = "waiting" if waiting else "failed" if failed else "ran"
    started = worker.started_at or (steps[0].started_at if steps else None)
    if worker.started_at and worker.finished_at:
        duration = int((worker.finished_at - worker.started_at).total_seconds() * 1000)
    else:
        duration = sum(step.duration_ms for step in steps)
    error = next((step.summary for step in steps if step.is_error and step.summary), None)
    reason = clipped(error or f"The helper ended as {worker.status}.", NOTE_ITEM_CHARS) if state == "failed" else None
    return RouteStopOut(
        id=f"worker:{worker.run_id}",
        stage="worker",
        lane="worker",
        line=f"run-{worker.run_id}",
        parent=parent,
        label=worker.label or "Helper",
        count=len(steps),
        state=state,
        reason=reason,
        detail=clipped(worker.task, NOTE_ITEM_CHARS) if worker.task else None,
        started_at=started,
        duration_ms=duration,
        cost=summed_cost(steps),
        tokens=summed_tokens(steps),
        checks=unique(checks),
        items=clipped_items(tool_items(steps)),
        run_id=worker.run_id,
        step_id=steps[0].step_id if steps else None,
    )


def use_stop(use: UtilityUse, key: str) -> RouteStopOut:
    return RouteStopOut(
        id=key,
        stage="afterwards",
        lane="afterwards",
        parent="answer",
        label=AFTERWARDS_LABELS.get(use.task, humanised(use.task)),
        detail=clipped(use.summary, NOTE_ITEM_CHARS) if use.summary else use.model,
        started_at=use.created_at,
        cost=use.cost,
    )


def afterwards_stops(uses: list[UtilityUse], notes: list[RouteStep]) -> list[RouteStopOut]:
    """Work done for the reply once it was shown, from the usage records and the notes made afterwards. A note about
    the same task as a usage record (a summary) adds its reason to that stop."""
    stops: dict[str, RouteStopOut] = {}
    for use in uses:
        if use.task in BEFORE_REPLY_TASKS:
            continue
        key, number = f"after:{use.task}", 2
        while key in stops:
            key, number = f"after:{use.task}:{number}", number + 1
        stops[key] = use_stop(use, key)
    for step in notes:
        if step.note is None:
            continue
        kind = step.note.kind
        stop = stops.get(f"after:{kind}")
        if stop is None:
            stop = stops[f"after:{kind}"] = RouteStopOut(
                id=f"after:{kind}",
                stage="afterwards",
                lane="afterwards",
                parent="answer",
                label=AFTERWARDS_LABELS.get(kind, humanised(kind)),
                started_at=step.started_at,
                run_id=step.run_id,
                step_id=step.step_id,
            )
        stop.detail = step.note.text
        stop.why = why_out(step.note.why) if step.note.why else None
        stop.cost = stop.cost if stop.cost is not None else step.note.cost
    return list(stops.values())
