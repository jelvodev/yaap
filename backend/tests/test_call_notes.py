"""Step notes made where things happen: tool calls, masked output, memory recalled, the cache lifetime."""

import asyncio
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from app.agent.call_notes import blocked_note, guard_note, masking_note, record_note
from app.agent.gate import call_tool
from app.agent.runs import RunConfig, RunRegistry, RunServices
from app.agent.tools import tools_for
from app.checkpoints.service import call_key
from app.core.cache_policy import CacheChoice
from app.core.outgoing_checks import OutgoingBlock
from app.core.reply_notes import cache_note, memory_note
from app.core.why import Why
from app.projects.memory import Entry, Recall
from app.secrets.service import masked_names
from app.tracing.notes import Note
from app.tracing.recorder import NoteStep, ToolStep

SECRET = "s3cr3t-value/with+chars=="


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("nothing here", []),
        (f"token {SECRET} end", ["TOKEN"]),
        ("url form s3cr3t-value%2Fwith%2Bchars%3D%3D", ["TOKEN"]),
        ("base64 form czNjcjN0LXZhbHVlL3dpdGgrY2hhcnM9PQ==", ["TOKEN"]),
        ("base64 without padding czNjcjN0LXZhbHVlL3dpdGgrY2hhcnM9PQ", ["TOKEN"]),
        (f"{SECRET} and other-secret-value", ["OTHER", "TOKEN"]),
        ("", []),
    ],
)
def test_masked_names_lists_the_secrets_found_in_any_masked_form(text, expected):
    assert masked_names(text, {"TOKEN": SECRET, "OTHER": "other-secret-value"}) == expected


def test_masked_names_finds_nothing_without_secrets():
    assert masked_names(SECRET, {}) == []


@pytest.mark.parametrize(
    "values",
    [[SECRET], ["another-long-value", "third-value-here"], ["a-value%with/odd chars"]],
)
def test_a_masking_note_never_holds_a_secret_value(values):
    secrets = {f"NAME{index}": value for index, value in enumerate(values)}
    names = masked_names(" ".join(values), secrets)
    detail = masking_note("run_command", names).detail()

    flat = str(detail)
    assert all(value not in flat for value in values)
    assert detail["items"] == names and detail["count"] == len(values)


@pytest.mark.parametrize(
    ("names", "text"),
    [
        (["A"], "The output of run_command held the value of 1 secret; it was replaced by a placeholder."),
        (["A", "B"], "The output of run_command held the value of 2 secrets; they were replaced by placeholders."),
    ],
)
def test_the_masking_note_counts_the_secrets_by_name(names, text):
    note = masking_note("run_command", names)

    assert note.kind == "secrets_masked" and note.text == text and note.why.source == "outgoing"


@pytest.mark.parametrize(
    ("suspicion", "fragment"),
    [(None, "found nothing suspicious"), ("It asks to send the file elsewhere.", "marked the call to run_command")],
)
def test_the_guard_note_says_what_the_check_found(suspicion, fragment):
    note = guard_note("run_command", suspicion)

    assert note.kind == "guard" and fragment in note.text
    assert note.why.setting == "injection_check" and (suspicion is None or suspicion in note.text)


def test_the_guard_note_keeps_a_long_finding_short():
    assert len(guard_note("run_command", "word " * 200).text) < 300


def test_the_blocked_note_names_the_tool_and_not_the_matched_text():
    block = OutgoingBlock("Blocked: the text matches the pattern `hunter2`.", Why("Nothing leaves.", "outgoing"))

    note = blocked_note("read_file", block)

    assert note.kind == "outgoing_blocked" and "read_file" in note.text and "hunter2" not in str(note.detail())
    assert note.why == block.why


def entry(section: str, text: str, start: int = 0) -> Entry:
    return Entry(section, text, start, start + 1)


@pytest.mark.parametrize(
    ("recalled", "text", "count", "has_why"),
    [
        (Recall("", True, 0), None, None, False),
        (Recall("m", True, 1), "The whole project memory went along (1 entry).", 1, False),
        (Recall("m", True, 5), "The whole project memory went along (5 entries).", 5, False),
        (
            Recall("m", False, 40, [entry("Facts", "a")], [entry("Rules", "b")]),
            "2 of 40 memory entries were recalled for this message.",
            2,
            True,
        ),
    ],
)
def test_the_memory_note_says_whole_or_how_many_entries_were_recalled(recalled, text, count, has_why):
    note = memory_note(recalled)

    if text is None:
        assert note is None
        return
    assert note.kind == "memory" and note.text == text and note.count == count and (note.why is not None) == has_why


def test_the_memory_note_lists_the_chosen_entries_with_their_section():
    note = memory_note(Recall("m", False, 9, [entry("Facts", "uses  Postgres\n")], [entry("Rules", "x" * 500)]))

    assert note.items[0] == "Facts: uses Postgres" and len(note.items[1]) <= 120
    assert "4,000" in note.why.rule


@pytest.mark.parametrize(("ttl", "text"), [("1h", "Prompt cache: 1 hour"), ("5m", "Prompt cache: 5 minutes")])
def test_the_cache_note_names_the_lifetime_and_keeps_the_reason(ttl, text):
    why = Why("Because.", "cache")

    assert cache_note(CacheChoice(ttl, why)) == Note("cache", text, why)


@dataclass
class FakeTrace:
    """Stands in for the run's recorder: keeps what the gate records."""

    steps: list[ToolStep] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    position: int = 0

    def reserve(self, run_id: int) -> int:
        self.position += 1
        return self.position

    async def record(self, run_id: int, step: ToolStep | NoteStep) -> None:
        if isinstance(step, ToolStep):
            self.steps.append(step)

    async def note(self, run_id: int, note: Note) -> None:
        self.notes.append(note)


def traced_run(folder: Path, trace: FakeTrace, **config):
    registry = RunRegistry()
    run = registry.start(
        RunConfig(
            conversation_id=1,
            environment_id=1,
            project_id=1,
            project_folder=folder,
            blueprint_layers=[],
            tool_names=tools_for(has_project=True, has_attachments=False),
            **config,
        ),
        RunServices(trace=trace),
    )
    run.state.record_id = 7
    return registry, run


COMMAND = {"command": "echo hi"}


def test_a_refused_call_keeps_its_reason_and_call_key_on_its_step(tmp_path):
    trace = FakeTrace()
    registry, run = traced_run(tmp_path, trace, unattended=True)

    output, is_error = asyncio.run(call_tool(registry, run, "run_command", COMMAND))

    (step,) = trace.steps
    assert is_error and step.approval == "asked"
    assert step.why is not None and step.call_key == call_key("run_command", COMMAND)


def test_an_approval_request_carries_the_reason_and_the_step_keeps_it(tmp_path):
    trace = FakeTrace()
    registry, run = traced_run(tmp_path, trace)
    seen: list[dict] = []

    async def scenario():
        run.services = replace(run.services, events=asyncio.Queue())
        event = None

        async def approve():
            nonlocal event
            event = (await run.events.get()).model_dump()
            registry.decide(event["approval_id"], "approve")

        task = asyncio.create_task(approve())
        await call_tool(registry, run, "run_command", COMMAND)
        await task
        seen.append(event)

    asyncio.run(asyncio.wait_for(scenario(), timeout=30))

    (event,) = seen
    (step,) = trace.steps
    assert event["why"] is not None and event["why"]["source"] == "permission"
    assert step.why is not None and step.why.stored() == event["why"] and step.approval == "approved"


def test_a_call_that_needs_no_decision_keeps_no_reason(tmp_path):
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
    trace = FakeTrace()
    registry, run = traced_run(tmp_path, trace)

    asyncio.run(call_tool(registry, run, "read_file", {"path": "a.txt"}))

    (step,) = trace.steps
    assert step.why is None and step.call_key is None


def test_masked_output_is_noted_by_secret_name_and_stays_masked(tmp_path):
    (tmp_path / "a.txt").write_text(f"key={SECRET}", encoding="utf-8")
    trace = FakeTrace()
    registry, run = traced_run(tmp_path, trace)
    run.services = replace(run.services, secrets={"TOKEN": SECRET})

    output, _ = asyncio.run(call_tool(registry, run, "read_file", {"path": "a.txt"}))

    (note,) = trace.notes
    assert SECRET not in str(output) and "[secret:TOKEN]" in str(output)
    assert note.kind == "secrets_masked" and note.items == ("TOKEN",) and SECRET not in str(note.detail())


def test_a_note_is_not_recorded_for_a_run_without_a_trace(tmp_path):
    registry, run = traced_run(tmp_path, FakeTrace())
    run.state.record_id = None

    asyncio.run(record_note(run, Note("guard", "x")))

    assert run.trace.notes == []
