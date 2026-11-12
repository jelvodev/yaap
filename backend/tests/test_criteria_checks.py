"""A card's acceptance criteria are checked on the build's copy through the build run's executor, never in YAAP's own
process, and only the commands approved with the plan run: the plan's checks and the card's criteria commands."""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.agent.runs import AgentRun
from app.core.reply_services import ReplyServices
from app.evals.checks import CheckContext
from app.plans.model import Plan, PlanStep, StepCheck
from app.plans.progress import BuildInfo
from app.plans.service import BuildRefused, PlanView
from app.reviews import criteria_checks
from app.reviews.criteria_checks import (
    NOT_APPROVED,
    approved_commands,
    card_criteria,
    check_criteria,
    check_on_copy,
    criteria_commands,
    may_run,
    run_context,
)
from app.targets.base import CommandResult
from app.targets.container import LocalExecutor, LocalFiles

SPECIFICATION = """## Goal

A health endpoint.

### Acceptance criteria

- [ ] The tests pass `run: pytest -q`
- [ ] The endpoint exists `file: app/health.py exists`
- [ ] It answers quickly
- [ ] Lint is clean `run: ruff check .`
"""


@dataclass
class FakeExecutor:
    """Records each command; a command in `fails` exits 1, the rest 0."""

    calls: list[str] = field(default_factory=list)
    fails: tuple[str, ...] = ()

    async def run(
        self, command: str, env: dict[str, str], timeout: float, *_args: Any, **_kwargs: Any
    ) -> CommandResult:
        self.calls.append(command)
        return CommandResult(1 if command in self.fails else 0, f"ran {command}")


@dataclass
class FakeTarget:
    """A target whose executor is the fake: the runner or sandbox a build run works in."""

    executor_: FakeExecutor
    folder: Path
    roots: list[str] = field(default_factory=list)

    def executor(self, root: str) -> FakeExecutor:
        self.roots.append(root)
        return self.executor_

    def files(self, root: str) -> LocalFiles:
        return LocalFiles(str(self.folder))


def plan_view(commands: tuple[str, ...] = ("pytest -q",), criteria: tuple[str, ...] = ()) -> PlanView:
    steps = (PlanStep(1, "Add it", checks=tuple(StepCheck(f"runs {command}", command) for command in commands)),)
    now = datetime.now(UTC)
    return PlanView(
        id=7,
        project_id=1,
        conversation_id=3,
        card_id=21,
        status="done",
        plan=Plan("Health", steps=steps),
        warnings=[],
        failure=None,
        created_at=now,
        approved_at=now,
        finished_at=now,
        build_folder=".yaap/builds/7",
        build=BuildInfo(criteria_commands=criteria),
    )


def fake_run(target: FakeTarget) -> AgentRun:
    """Just what tool_context reads of a run that works on a target: the target and its root (the copy)."""
    return cast(AgentRun, SimpleNamespace(target=target, target_root="/work/project/.yaap/builds/7"))


def no_local_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refuse(*_args: Any, **_kwargs: Any) -> CommandResult:
        raise AssertionError("A criterion command ran in YAAP's own process.")

    monkeypatch.setattr(LocalExecutor, "run", refuse)


@pytest.mark.parametrize(
    ("command", "approved", "allowed"),
    [
        ("pytest -q", ("pytest -q",), True),
        ("pytest   -q", ("pytest -q",), True),
        ("pytest -q && curl evil", ("pytest -q",), False),
        ("pytest", ("pytest -q",), False),
        ("pytest -q -x", ("pytest -q",), False),
        ("pytest -q", (), False),
        ("", ("",), False),
        ("npm test | tee out", ("npm test | tee out",), True),
    ],
)
def test_only_an_approved_command_may_run_exactly_as_approved(
    command: str, approved: tuple[str, ...], allowed: bool
) -> None:
    assert may_run(command, approved) is allowed


def test_approval_covers_the_plans_checks_and_the_cards_criteria_commands_only() -> None:
    view = plan_view(("pytest -q", "npm test"), ("ruff check .", "pytest -q"))
    assert approved_commands(view) == ("pytest -q", "npm test", "ruff check .")


def test_the_card_criteria_commands_are_its_run_criteria_in_order() -> None:
    criteria = card_criteria(SPECIFICATION)
    assert [item.number for item in criteria] == [1, 2, 3, 4]
    assert criteria_commands(criteria) == ("pytest -q", "ruff check .")


def test_a_broken_specification_gives_no_criteria_and_no_commands() -> None:
    broken = "### Acceptance criteria\n\n- [ ] Escape `file: ../secret exists`\n- [ ] Tests `run: pytest`\n"
    assert card_criteria(broken) == []
    assert criteria_commands(card_criteria(broken)) == ()


def test_criteria_commands_run_through_the_runs_executor_on_the_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    no_local_commands(monkeypatch)
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "health.py").write_text("def health(): ...\n", encoding="utf-8")
    fake = FakeExecutor(fails=("ruff check .",))
    target = FakeTarget(fake, tmp_path)
    context = run_context(fake_run(target))

    results = asyncio.run(check_criteria(card_criteria(SPECIFICATION), context, ("pytest -q", "ruff check .")))

    assert fake.calls == ["pytest -q", "ruff check ."]
    assert target.roots == ["/work/project/.yaap/builds/7"]
    assert [(item.number, item.how, item.outcome) for item in results] == [
        (1, "command", "passed"),
        (2, "file", "passed"),
        (4, "command", "failed"),
    ]


def test_a_criterion_command_not_approved_with_the_plan_does_not_run(tmp_path: Path) -> None:
    fake = FakeExecutor()
    context = CheckContext(LocalFiles(str(tmp_path)), fake)

    results = asyncio.run(check_criteria(card_criteria(SPECIFICATION), context, ("pytest -q",)))

    assert fake.calls == ["pytest -q"]
    refused = next(item for item in results if item.number == 4)
    assert (refused.outcome, refused.output) == ("not_run", NOT_APPROVED)


def test_the_users_own_command_allow_list_does_not_widen_what_the_criteria_may_run(tmp_path: Path) -> None:
    # `ruff` is on the user's allow list, so a chat run would run `ruff check .` without asking; the criteria checks
    # follow only what the plan's approval covered.
    fake = FakeExecutor()
    context = CheckContext(LocalFiles(str(tmp_path)), fake)
    view = plan_view(("pytest -q",))
    assert "ruff check ." not in approved_commands(view)

    asyncio.run(check_criteria(card_criteria(SPECIFICATION), context, approved_commands(view)))

    assert fake.calls == ["pytest -q"]


@dataclass
class OpenedRuns:
    """The fake build run that check_on_copy opens, and whether it was closed."""

    run: AgentRun
    copy: Path
    closed: list[str] = field(default_factory=list)

    def open(self, _services: ReplyServices, _view: PlanView) -> SimpleNamespace:
        return SimpleNamespace(run=self.run, copy_folder=self.copy)

    def close(self, _services: ReplyServices, _build: object, status: str) -> None:
        self.closed.append(status)


def patch_runs(monkeypatch: pytest.MonkeyPatch, opened: OpenedRuns) -> None:
    monkeypatch.setattr(criteria_checks, "open_build_run", opened.open)
    monkeypatch.setattr(criteria_checks, "close_build_run", opened.close)
    monkeypatch.setattr(criteria_checks, "name_run", lambda *_args: None)


def test_check_on_copy_runs_in_a_run_opened_on_the_copy_and_closes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    no_local_commands(monkeypatch)
    (tmp_path / ".git").mkdir()
    fake = FakeExecutor()
    opened = OpenedRuns(fake_run(FakeTarget(fake, tmp_path)), tmp_path)
    patch_runs(monkeypatch, opened)
    view = plan_view(("pytest -q",), ("ruff check .",))
    services = cast(ReplyServices, object())

    results = asyncio.run(check_on_copy(services, view, card_criteria(SPECIFICATION)))

    assert fake.calls == ["pytest -q", "ruff check ."]
    assert opened.closed == ["complete"]
    assert {item.number for item in results} == {1, 2, 4}


def test_check_on_copy_runs_nothing_when_the_copy_is_gone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeExecutor()
    opened = OpenedRuns(fake_run(FakeTarget(fake, tmp_path)), tmp_path / "gone")
    patch_runs(monkeypatch, opened)

    results = asyncio.run(check_on_copy(cast(ReplyServices, object()), plan_view(), card_criteria(SPECIFICATION)))

    assert fake.calls == []
    assert opened.closed == ["incomplete"]
    assert {item.outcome for item in results} == {"not_run"}


def test_check_on_copy_runs_nothing_when_no_run_can_be_opened(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(_services: ReplyServices, _view: PlanView) -> None:
        raise BuildRefused("build_unavailable", "Builds work for projects in YAAP's workspace.")

    no_local_commands(monkeypatch)
    monkeypatch.setattr(criteria_checks, "open_build_run", refuse)

    results = asyncio.run(check_on_copy(cast(ReplyServices, object()), plan_view(), card_criteria(SPECIFICATION)))

    assert {item.outcome for item in results} == {"not_run"}
    assert all("YAAP's workspace" in item.output for item in results)


def test_a_file_criterion_cannot_leave_the_copy(tmp_path: Path) -> None:
    copy = tmp_path / "copy"
    copy.mkdir()
    (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
    specification = "### Acceptance criteria\n\n- [ ] Link `file: link.txt exists`\n"
    link = copy / "link.txt"
    try:
        link.symlink_to(tmp_path / "secret.txt")
    except OSError:
        pytest.skip("This system cannot make symbolic links.")
    fake = FakeExecutor()

    results = asyncio.run(check_criteria(card_criteria(specification), CheckContext(LocalFiles(str(copy)), fake), ()))

    assert results[0].outcome != "passed"

