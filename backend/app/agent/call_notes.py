"""Step notes of a tool call: the injection guard's check, secrets masked in output, output the outgoing check blocked.

The builders are pure; `record_note` writes a note to the run's trace when the run has one.
"""

from app.agent.runs import AgentRun
from app.core.outgoing_checks import OutgoingBlock
from app.core.why import Why
from app.tracing.notes import Note

GUARD_WHY = Why(
    "After the run read outside content, the utility model checks each call that acts before you approve it.",
    "guard",
    "injection_check",
)
MASKING_WHY = Why("Secret values never go back to the model: YAAP replaces them in tool output.", "outgoing")
FINDING_CHARS = 200


def guard_note(tool: str, suspicion: str | None) -> Note:
    """The injection check's result for a call: nothing suspicious, or the finding."""
    if suspicion is None:
        return Note("guard", f"The injection check found nothing suspicious in the call to {tool}.", GUARD_WHY)
    finding = " ".join(suspicion.split())
    if len(finding) > FINDING_CHARS:
        finding = finding[: FINDING_CHARS - 1] + "…"
    return Note("guard", f"The injection check marked the call to {tool} as suspicious: {finding}", GUARD_WHY)


def masking_note(tool: str, names: list[str]) -> Note:
    """Secrets replaced in a tool's output; only their names are kept, never a value."""
    noun = "secret" if len(names) == 1 else "secrets"
    text = f"The output of {tool} held the value of {len(names)} {noun}; it was replaced by a placeholder."
    if len(names) > 1:
        text = text.replace("it was", "they were").replace("a placeholder", "placeholders")
    return Note("secrets_masked", text, MASKING_WHY, items=tuple(names), count=len(names))


def blocked_note(tool: str, block: OutgoingBlock) -> Note:
    """Tool output the outgoing check kept from the model; the matched text is never kept."""
    return Note("outgoing_blocked", f"The output of {tool} was not passed on: the outgoing check blocked it.", block.why)


async def record_note(run: AgentRun, note: Note) -> None:
    """Write the note to the run's trace; nothing when the run records none."""
    if run.trace is None or run.state.record_id is None:
        return
    await run.trace.note(run.state.record_id, note)
