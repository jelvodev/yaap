"""Step notes made around a reply: memory recalled, the cache lifetime chosen, the trim and summary after a reply.

The builders are pure. The recording helpers write to the reply's run, which is the root of its trace.
"""

from collections.abc import Iterable

from sqlalchemy.orm import Session

from app.core.cache_policy import CacheChoice
from app.core.why import Why
from app.projects.memory import RECALL_TOKENS, Recall
from app.tracing.notes import Note, append_note
from app.tracing.provider import ModelTrace
from app.tracing.steps import reply_run_id

ENTRY_CHARS = 120
TRIM_TEXT = "Long output of older turns is shortened from the next reply on."
SUMMARY_TEXT = "The earlier messages were summarised; the next replies continue from the summary."


def memory_note(recalled: Recall) -> Note | None:
    """What of the project memory went along with this message; None when the memory has no entries."""
    if recalled.total == 0:
        return None
    if recalled.whole:
        noun = "entry" if recalled.total == 1 else "entries"
        return Note("memory", f"The whole project memory went along ({recalled.total} {noun}).", count=recalled.total)
    chosen = [*recalled.recent, *recalled.related]
    items = tuple(f"{entry.section}: {' '.join(entry.text.split())}"[:ENTRY_CHARS] for entry in chosen)
    rule = (
        f"The project memory is larger than about {RECALL_TOKENS:,} tokens, so each message gets the newest entries "
        "of every section and the entries closest to it."
    )
    return Note(
        "memory",
        f"{len(chosen)} of {recalled.total} memory entries were recalled for this message.",
        Why(rule, "memory"),
        items=items,
        count=len(chosen),
    )


def cache_note(choice: CacheChoice) -> Note:
    text = "Prompt cache: 1 hour" if choice.ttl == "1h" else "Prompt cache: 5 minutes"
    return Note("cache", text, choice.why)


def record_reply_notes(trace: ModelTrace, notes: Iterable[Note]) -> None:
    """Write the notes made before the reply streams to its run. Called from the worker thread that prepares it."""
    for note in notes:
        trace.recorder.note_now(trace.run_id, note)


def note_on_reply(session: Session, conversation_id: int, message_id: int, note: Note) -> bool:
    """Write a note after the last step of the run that produced this reply; nothing when it has no run."""
    run_id = reply_run_id(session, conversation_id, message_id)
    if run_id is None:
        return False
    append_note(session, run_id, note)
    return True
