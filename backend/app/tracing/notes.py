"""Step notes: what happened around a reply, kept on the trace beside the model and tool calls.

A note is a `RunStep` of kind "note": memory recalled, old output trimmed, a summary, secret values masked in tool
output, an outgoing check that blocked tool output, the cache lifetime chosen, the injection guard's check. Its
`detail` column holds the note as JSON. A tool step that asked or was refused keeps its reason (and the call key) in
the same column.

This module is a leaf of the tracing package: the recorder imports it, so it must not import the recorder. That is
why `ApprovalOutcome` lives here; the recorder and `tracing.steps` re-export it under the same name.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, get_args

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.why import Why, why_from
from app.storage.models import RunStep

NOTE_KIND = "note"
NoteKind = Literal["memory", "trim", "summary", "secrets_masked", "outgoing_blocked", "cache", "guard"]
NOTE_KINDS: tuple[NoteKind, ...] = get_args(NoteKind)
# What happened to a call that needed a decision: the user approved or declined it, the policy refused it, or it
# would have asked in an unattended run (a scheduled task, an eval case), where nobody can approve, and was refused.
# A call that ran without asking has none.
ApprovalOutcome = Literal["approved", "declined", "refused", "asked"]
NOTE_TEXT_CHARS = 300
NOTE_ITEM_CHARS = 200
NOTE_MAX_ITEMS = 20


def approval_outcome(value: str | None) -> ApprovalOutcome | None:
    for outcome in get_args(ApprovalOutcome):
        if value == outcome:
            return outcome
    return None


def clipped(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


@dataclass(frozen=True)
class Note:
    kind: NoteKind
    text: str
    why: Why | None = None
    cost: float | None = None
    # Short entries the label opens to: recalled memory entries, the NAMES of masked secrets (never their values).
    items: tuple[str, ...] = ()
    # A number that goes with the text: entries recalled, values masked.
    count: int | None = None

    def detail(self) -> dict[str, Any]:
        """The JSON kept in `RunStep.detail`."""
        return {
            "note": self.kind,
            "text": clipped(self.text, NOTE_TEXT_CHARS),
            "why": self.why.stored() if self.why is not None else None,
            "cost": self.cost,
            "items": [clipped(item, NOTE_ITEM_CHARS) for item in self.items[:NOTE_MAX_ITEMS]],
            "count": self.count,
        }


def note_from(detail: object) -> Note | None:
    """A stored note read back; None when the value is not a valid note."""
    if not isinstance(detail, dict):
        return None
    kind = next((known for known in NOTE_KINDS if known == detail.get("note")), None)
    text = detail.get("text")
    if kind is None or not isinstance(text, str):
        return None
    cost, count, raw_items = detail.get("cost"), detail.get("count"), detail.get("items")
    items = tuple(item for item in raw_items if isinstance(item, str)) if isinstance(raw_items, list) else ()
    return Note(
        kind=kind,
        text=text,
        why=why_from(detail.get("why")),
        cost=float(cost) if isinstance(cost, int | float) and not isinstance(cost, bool) else None,
        items=items,
        count=count if isinstance(count, int) and not isinstance(count, bool) else None,
    )


@dataclass(frozen=True)
class NoteView:
    step_id: int
    run_id: int
    position: int
    started_at: datetime
    note: Note


@dataclass(frozen=True)
class CallWhy:
    """Why a tool call asked or was refused, with the key of the call it belongs to."""

    step_id: int
    run_id: int
    tool: str | None
    call_key: str | None
    approval: ApprovalOutcome | None
    why: Why


def run_notes(session: Session, run_ids: Iterable[int]) -> list[NoteView]:
    """All notes of these runs, in order of run, position and id."""
    ids = list(run_ids)
    if not ids:
        return []
    rows = session.scalars(
        select(RunStep)
        .where(RunStep.run_id.in_(ids), RunStep.kind == NOTE_KIND)
        .order_by(RunStep.run_id, RunStep.position, RunStep.id)
    )
    views: list[NoteView] = []
    for row in rows:
        note = note_from(row.detail)
        if note is not None:
            views.append(NoteView(row.id, row.run_id, row.position, row.started_at, note))
    return views


def call_whys(session: Session, run_ids: Iterable[int]) -> list[CallWhy]:
    """The tool steps of these runs that kept a reason, in order of run, position and id."""
    ids = list(run_ids)
    if not ids:
        return []
    rows = session.scalars(
        select(RunStep)
        .where(RunStep.run_id.in_(ids), RunStep.kind == "tool", RunStep.detail.is_not(None))
        .order_by(RunStep.run_id, RunStep.position, RunStep.id)
    )
    found: list[CallWhy] = []
    for row in rows:
        detail = row.detail
        if not isinstance(detail, dict):
            continue
        why = why_from(detail.get("why"))
        if why is None:
            continue
        call_key = detail.get("call_key")
        found.append(
            CallWhy(
                row.id,
                row.run_id,
                row.tool,
                call_key if isinstance(call_key, str) else None,
                approval_outcome(row.approval),
                why,
            )
        )
    return found


def note_row(run_id: int, position: int, started_at: datetime, note: Note) -> RunStep:
    return RunStep(
        run_id=run_id,
        position=position,
        kind=NOTE_KIND,
        started_at=started_at,
        duration_ms=0,
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=0,
        is_error=False,
        summary=clipped(note.text, NOTE_TEXT_CHARS),
        detail=note.detail(),
    )


def append_note(session: Session, run_id: int, note: Note) -> None:
    """Write a note after the last step of a run, for a note made once the run has ended (a trim decided after a
    reply). Commits."""
    last = session.scalar(select(func.max(RunStep.position)).where(RunStep.run_id == run_id)) or 0
    session.add(note_row(run_id, last + 1, datetime.now(UTC), note))
    session.commit()
