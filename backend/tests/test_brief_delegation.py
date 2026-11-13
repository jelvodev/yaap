"""Delegation by brief: how many sub-runs overlap, the check through the gate, the escalation, the tool offered."""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent import brief_delegation
from app.agent.brief import Brief
from app.agent.brief_delegation import CHECK_TIMEOUT_SECONDS, delegate_by_brief, escalation_note, run_check
from app.agent.delegation import Delegator, SubRunResult
from app.agent.delegation_tools import BRIEF_DELEGATE, FREE_DELEGATE, delegate
from app.agent.policy import ToolPolicy
from app.agent.runs import RunConfig, RunRegistry, RunServices
from app.agent.sub_run_report import CheckResult
from app.agent.toolkit import ToolError
from app.agent.tools import own_tools, tools_for
from app.targets.base import CommandResult
from app.tracing.notes import Note

GOOD_REPORT = "Status: done\nConclusion: Fixed.\nChanged files: a.py\nLeft open: nothing"


def result(model: str = "worker-model", status: str = "complete", record_id: int | None = 1, **extra: Any):
    return SubRunResult(status, model, "worker", GOOD_REPORT, [], record_id, "work", GOOD_REPORT, **extra)


def delegator(delegation: str) -> Delegator:
    # Only the lock, the readers and the cached mode are used; the rest is not touched by these tests.
    unused: Any = None
    return Delegator(
        session_factory=unused,
        cipher=unused,
        credential_types={},
        providers=unused,
        runs=unused,
        internal_url="",
        layers=[],
        project_text=None,
        working_folder=None,
        languages=unused,
        preset=None,
        model="main-model",
        effort="low",
        delegation=delegation,  # type: ignore[arg-type] - a parametrised literal
    )


def parent_stub(**extra: Any) -> Any:
    return SimpleNamespace(parent_token=None, environment_id=1, **extra)


@pytest.mark.parametrize(
    ("delegation", "mode", "peak"),
    [("brief", "explore", 3), ("brief", "work", 1), ("free", "explore", 1), ("free", "work", 1)],
)
def test_sub_runs_overlap_only_for_exploring_by_brief_and_at_most_three(monkeypatch, delegation, mode, peak):
    active = highest = 0

    async def fake_run_sub_run(self, parent, message, agent_name, role, worker=None, mode="work", instructions=None):
        nonlocal active, highest
        active += 1
        highest = max(highest, active)
        await asyncio.sleep(0.01)
        active -= 1
        return result()

    monkeypatch.setattr(Delegator, "run_sub_run", fake_run_sub_run)
    lead = delegator(delegation)

    async def scenario():
        calls = (lead.delegate(parent_stub(), Brief(task=f"t{n}"), None, None, None, mode) for n in range(5))
        await asyncio.gather(*calls)

    asyncio.run(scenario())

    assert highest == peak


# The check through the gate.


@dataclass
class FakeExecutor:
    exit_code: int
    commands: list[str] = field(default_factory=list)

    async def run(self, command, environment, timeout, session, silence=0, on_output=None):
        self.commands.append(command)
        return CommandResult(self.exit_code, "output of the check")


def started_run(folder: Path, **config: Any):
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
        RunServices(),
    )
    return registry, run


def test_the_check_asks_like_any_command_when_the_policy_asks(tmp_path, monkeypatch):
    fake = FakeExecutor(0)
    monkeypatch.setattr("app.agent.command_tools.executor", lambda run: fake)
    registry, run = started_run(tmp_path)
    seen: list[dict] = []

    async def scenario():
        async def approve():
            event = (await run.events.get()).model_dump()
            seen.append(event)
            registry.decide(event["approval_id"], "approve")

        task = asyncio.create_task(approve())
        outcome = await run_check(registry, run, "pytest -q")
        await task
        return outcome

    outcome = asyncio.run(asyncio.wait_for(scenario(), timeout=30))

    assert seen[0]["event"] == "approval_request" and seen[0]["tool"] == "run_command"
    assert outcome == CheckResult("pytest -q", "passed", "Exit code 0\noutput of the check")
    assert fake.commands == ["pytest -q"]


@pytest.mark.parametrize(
    ("config", "exit_code", "outcome", "ran"),
    [
        ({"policy": ToolPolicy(access="full")}, 0, "passed", True),
        ({"policy": ToolPolicy(access="full")}, 1, "failed", True),
        ({"unattended": True}, 0, "not_run", False),
    ],
)
def test_the_check_outcome_follows_the_permission_and_the_exit_code(
    tmp_path, monkeypatch, config, exit_code, outcome, ran
):
    fake = FakeExecutor(exit_code)
    monkeypatch.setattr("app.agent.command_tools.executor", lambda run: fake)
    registry, run = started_run(tmp_path, **config)

    checked = asyncio.run(asyncio.wait_for(run_check(registry, run, "pytest -q"), timeout=30))

    assert checked.outcome == outcome and bool(fake.commands) == ran


def test_the_check_has_a_generous_timeout():
    assert CHECK_TIMEOUT_SECONDS == 600


# The escalation.


@dataclass
class NoteTrace:
    notes: list[Note] = field(default_factory=list)

    async def note(self, run_id: int, note: Note) -> None:
        self.notes.append(note)


@pytest.mark.parametrize(
    ("script", "roles", "notes", "checks", "attempts_line"),
    [
        (["passed"], ["worker"], 0, 1, "Attempts: 1 worker on worker-model (check passed)."),
        (["failed", "passed"], ["worker", "worker"], 1, 2, "(check failed), 2 worker on worker-model (check passed)."),
        (
            ["failed", "failed", "passed"],
            ["worker", "worker", "main"],
            2,
            3,
            "2 worker on worker-model (check failed), 3 main on main-model (check passed).",
        ),
        (["failed", "failed", "failed"], ["worker", "worker", "main"], 2, 3, "3 main on main-model (check failed)."),
        (["stopped"], ["worker"], 0, 0, "Attempts: 1 worker on worker-model (stopped)."),
    ],
)
def test_a_failed_check_goes_back_to_the_worker_once_and_then_to_the_main_model(
    monkeypatch, script, roles, notes, checks, attempts_line
):
    asked: list[tuple[str, str]] = []
    checked: list[str] = []
    logged: list[tuple[int, str, int | None]] = []
    outcomes = iter(script)
    current: list[str] = []

    async def fake_run_sub_run(self, parent, message, agent_name, role, worker=None, mode="work", instructions=None):
        asked.append((role, message))
        assert (mode, instructions) == ("work", "brief-worker")
        current.append(next(outcomes))
        model = "main-model" if role == "main" else "worker-model"
        return result(model, "incomplete" if current[-1] == "stopped" else "complete", len(asked))

    async def fake_run_check(runs, parent, command):
        checked.append(command)
        return CheckResult(command, current[-1], "FAILED test_a")

    monkeypatch.setattr(Delegator, "run_sub_run", fake_run_sub_run)
    monkeypatch.setattr(brief_delegation, "run_check", fake_run_check)
    monkeypatch.setattr(
        brief_delegation, "log_check", lambda lead, res, check, number, by, first: logged.append((number, by, first))
    )
    trace = NoteTrace()
    parent = parent_stub(trace=trace, state=SimpleNamespace(record_id=7))

    brief = Brief("Fix it", check="pytest -q")
    text = asyncio.run(delegate_by_brief(delegator("brief"), parent, brief, None, "worker", None))

    assert [role for role, _ in asked] == roles
    assert len(trace.notes) == notes and all(note.kind == "escalation" for note in trace.notes)
    assert len(checked) == checks and [number for number, _, _ in logged] == list(range(1, checks + 1))
    assert all(first == 1 for _, _, first in logged)
    assert attempts_line in text and text.startswith("Sub-run by worker on ")
    if len(asked) > 1:
        assert "did not pass the check" in asked[1][1] and "FAILED test_a" in asked[1][1]
    if len(asked) > 2:
        assert "A worker tried this twice" in asked[2][1]


def test_the_escalation_note_says_what_failed_and_where_it_goes_next():
    check = CheckResult("pytest -q", "failed", "x")

    retry = escalation_note("worker_retry", 1, check, "worker", "small")
    takeover = escalation_note("main", 2, check, "worker", "small")

    assert retry.kind == takeover.kind == "escalation" and retry.count == 1 and takeover.count == 2
    assert "goes back to it" in retry.text and "main model takes over" in takeover.text
    assert retry.items == ("check: pytest -q", "attempt 1: worker on small, check failed")
    assert retry.why is not None and retry.why.source == "delegation"


# The tool.


def test_the_brief_schema_is_offered_only_when_the_preset_delegates_by_brief(tmp_path):
    def delegate_tool(by_brief: bool):
        run = RunRegistry().start(
            RunConfig(
                conversation_id=1,
                environment_id=1,
                blueprint_layers=[],
                tool_names=tools_for(has_project=True, has_attachments=False),
                project_folder=tmp_path,
            ),
            RunServices(delegator=SimpleNamespace(by_brief=lambda environment_id: by_brief)),  # type: ignore[arg-type]
        )
        return next(tool for tool in own_tools(run) if tool.name == "delegate")

    assert delegate_tool(False) is FREE_DELEGATE
    assert delegate_tool(True) is BRIEF_DELEGATE
    assert {"files", "facts", "done_when", "check"} <= set(BRIEF_DELEGATE.input_schema["properties"])
    assert "check" not in FREE_DELEGATE.input_schema["properties"]


@dataclass
class RecordingDelegator:
    by_brief_mode: bool
    calls: list[tuple] = field(default_factory=list)

    def by_brief(self, environment_id: int) -> bool:
        return self.by_brief_mode

    async def delegate(self, parent, brief, agent_name, role, worker, mode):
        self.calls.append((brief, agent_name, role, worker, mode))
        return "report"


@pytest.mark.parametrize(
    ("by_brief", "arguments", "expected"),
    [
        (False, {"task": "Do it"}, (Brief(task="Do it"), None, "worker", None, "work")),
        (False, {"task": "Do it", "role": "main", "mode": "explore"}, (Brief("Do it"), None, "main", None, "explore")),
        (True, {"task": "Do it", "check": "pytest -q"}, (Brief("Do it", check="pytest -q"), None, None, None, "work")),
        (True, {"task": "Do it", "role": "worker", "agent": "b"}, (Brief(task="Do it"), "b", "worker", None, "work")),
    ],
)
def test_the_delegate_tool_reads_the_task_as_a_brief_and_the_role_as_asked(by_brief, arguments, expected):
    lead = RecordingDelegator(by_brief)
    run: Any = SimpleNamespace(delegator=lead, environment_id=1)

    assert asyncio.run(delegate(run, arguments)) == "report"
    assert lead.calls == [expected]


@pytest.mark.parametrize(
    ("by_brief", "arguments", "message"),
    [
        (False, {"task": "x", "role": "boss"}, "role must be"),
        (True, {"task": "x", "role": "boss"}, "role must be"),
        (True, {"task": "x", "files": "a.py"}, "list of strings"),
        (True, {"task": "x", "files": ["/etc/passwd"]}, "absolute path"),
        (True, {"check": "pytest"}, "`task` is required"),
        (True, {"task": "x", "mode": "plan"}, "mode must be"),
    ],
)
def test_the_delegate_tool_refuses_what_cannot_be_delegated(by_brief, arguments, message):
    run: Any = SimpleNamespace(delegator=RecordingDelegator(by_brief), environment_id=1)

    with pytest.raises(ToolError, match=message):
        asyncio.run(delegate(run, arguments))
