"""Eval case files (app/evals/cases.py, units.py): table-driven validation and reading from the project's layer."""

from pathlib import Path

import pytest
import yaml

from app.agent.policy import CaseApprovals
from app.evals.cases import parse_case, read_cases
from app.evals.models import Budget, CommandCheck, FileCheck, RubricCheck, StartState
from app.evals.units import UnitError, parse_cost, parse_seconds, parse_steps, parse_tokens

VALID_FRONT: dict[str, object] = {
    "summary": "Fix the date parser",
    "start": {"commit": "3f2a1bc"},
    "agent": "reviewer",
    "preset": "Cheap",
    "allowed_tools": ["read_file", "edit_file", "run_command"],
    "budget": {"steps": "40 steps", "tokens": "200k tokens", "cost": "0.50 USD", "time": "10 min"},
    "checks": [
        {"kind": "command", "run": "pytest -q tests/test_dates.py", "timeout": "5 min"},
        {"kind": "file", "path": "src/dates.py", "contains": "def parse_date", "not_matches": r"^\s*print\("},
        {"kind": "file", "path": "src/old_dates.py", "exists": False},
        {"kind": "rubric", "name": "explains", "rubric": "The reply names the cause.", "files": ["src/dates.py"]},
    ],
}
PROMPT = "The date parser fails on ISO weeks. Fix it and keep the tests green."


def case_text(front: dict[str, object] | None = None, body: str = PROMPT, **changes: object) -> str:
    values = dict(VALID_FRONT if front is None else front)
    for key, value in changes.items():
        if value is None:
            values.pop(key, None)
        else:
            values[key] = value
    return f"---\n{yaml.safe_dump(values, sort_keys=False)}---\n\n{body}\n"


def with_check(check: dict[str, object]) -> str:
    return case_text(checks=[check])


def test_a_valid_case_is_read_with_every_part():
    found = parse_case("date-parser", case_text())

    assert found.problems == () and found.case is not None
    case = found.case
    assert case.prompt == PROMPT
    assert case.start == StartState(commit="3f2a1bc")
    assert (case.summary, case.agent, case.preset) == ("Fix the date parser", "reviewer", "Cheap")
    assert case.allowed_tools == ("read_file", "edit_file", "run_command")
    assert case.budget == Budget(steps=40, tokens=200_000, cost_usd=0.5, seconds=600)
    command, content, absent, rubric = case.checks
    assert command == CommandCheck("command: pytest -q tests/test_dates.py", "pytest -q tests/test_dates.py", 300)
    assert isinstance(content, FileCheck) and content.contains == ("def parse_date",)
    assert content.not_matches == (r"^\s*print\(",)
    assert isinstance(absent, FileCheck) and not absent.exists
    assert rubric == RubricCheck("explains", "The reply names the cause.", ("src/dates.py",))
    assert found.path == ".blueprint/evals/date-parser.md"


def test_a_case_from_a_replay_starts_at_a_checkpoint_and_needs_only_checks():
    found = parse_case("replayed", case_text(start={"checkpoint": 42}, budget=None, agent=None, preset=None))

    assert found.case is not None
    assert found.case.start == StartState(checkpoint=42)
    assert found.case.budget is None and found.case.agent is None


@pytest.mark.parametrize(
    ("name", "text", "expected"),
    [
        ("missing-prompt", case_text(body="   "), "The prompt is missing"),
        ("unknown-kind", with_check({"kind": "shell", "run": "ls"}), "unknown kind 'shell'"),
        ("budget-number", case_text(budget={"steps": 40}), "'40' has no unit"),
        ("budget-text", case_text(budget={"tokens": "200000"}), "'200000' has no unit"),
        ("budget-unit", case_text(budget={"steps": "40 minutes"}), "unknown unit"),
        ("budget-zero", case_text(budget={"cost": "0 USD"}), "more than zero"),
        ("budget-part", case_text(budget={"money": "1 USD"}), "budget has unknown parts: money"),
        ("budget-empty", case_text(budget={}), "budget must name one or more"),
        ("no-start", case_text(start=None), "The start state is missing"),
        ("two-starts", case_text(start={"commit": "3f2a1bc", "checkpoint": 4}), "exactly one of commit"),
        ("branch", case_text(start={"commit": "main"}), "not a branch name"),
        ("checkpoint", case_text(start={"checkpoint": "four"}), "checkpoint's number"),
        ("typo", case_text(chekcs=[]), "Unknown keys in the front matter: chekcs"),
        ("nothing", case_text(checks=None, budget=None), "nothing to judge it by"),
        ("empty-checks", case_text(checks=[]), "at least one check"),
        ("check-text", case_text(checks=["pytest"]), "key: value pairs with a kind"),
        ("check-key", with_check({"kind": "command", "run": "ls", "cwd": "src"}), "unknown keys: cwd"),
        ("no-run", with_check({"kind": "command"}), "checks[1].run must be the command"),
        ("timeout-unit", with_check({"kind": "command", "run": "ls", "timeout": 300}), "'300' has no unit"),
        ("timeout-long", with_check({"kind": "command", "run": "ls", "timeout": "2 h"}), "at most 1 h"),
        ("escape", with_check({"kind": "file", "path": "../secret", "contains": "x"}), "without .. or .git"),
        ("absolute", with_check({"kind": "file", "path": "/etc/passwd", "contains": "x"}), "relative path"),
        ("git", with_check({"kind": "file", "path": ".git/config", "contains": "x"}), "without .. or .git"),
        ("no-condition", with_check({"kind": "file", "path": "a.py"}), "needs contains"),
        ("absent-content", with_check({"kind": "file", "path": "a", "exists": False, "contains": "x"}), "cannot also"),
        ("regex", with_check({"kind": "file", "path": "a.py", "matches": "(unclosed"}), "does not compile"),
        ("condition-type", with_check({"kind": "file", "path": "a.py", "contains": 3}), "text or a list of texts"),
        ("no-rubric", with_check({"kind": "rubric", "files": ["a.py"]}), "rubric must say"),
        ("rubric-path", with_check({"kind": "rubric", "rubric": "ok", "files": ["../x"]}), "without .. or .git"),
        ("tools", case_text(allowed_tools="read_file"), "allowed_tools must be a list"),
        ("tool-name", case_text(allowed_tools=["read file"]), "not tool names: read file"),
        ("agent", case_text(agent="Code Reviewer"), "agent must be the name of an agent file"),
        ("Bad_Name", case_text(), "The file name must be lowercase"),
        ("yaml", "---\nstart: [unclosed\n---\nDo it.\n", "not valid YAML"),
        ("no-front-matter", "Just a prompt.\n", "The start state is missing"),
    ],
)
def test_an_invalid_case_file_is_refused_with_the_reason(name, text, expected):
    found = parse_case(name, text)

    assert found.case is None
    assert any(expected in problem for problem in found.problems), found.problems


@pytest.mark.parametrize(
    ("approve", "expected"),
    [
        ({"tools": ["edit_file", "write_file"]}, CaseApprovals(frozenset({"edit_file", "write_file"}), ())),
        ({"commands": ["python3 -m unittest"]}, CaseApprovals(frozenset(), ("python3 -m unittest",))),
        (
            {"tools": ["git_commit"], "commands": ["pytest  -q"]},
            CaseApprovals(frozenset({"git_commit"}), ("pytest -q",)),
        ),
    ],
)
def test_a_valid_approve_is_read_as_the_calls_the_play_approves(approve, expected):
    found = parse_case("approves", case_text(approve=approve))

    assert found.problems == ()
    assert found.case is not None and found.case.approve == expected


def test_a_case_without_approve_approves_nothing():
    found = parse_case("plain", case_text())

    assert found.case is not None and found.case.approve is None


@pytest.mark.parametrize(
    "approve",
    [
        {"tools": ["web_fetch"]},
        {"tools": ["git_push"]},
        {"tools": ["run_command"]},
        {"tools": ["no_such_tool"]},
        {"commands": ["make test && make deploy"]},
        {"commands": "pytest"},
        {"tools": "edit_file"},
        {"other": ["edit_file"]},
        ["edit_file"],
        {},
    ],
)
def test_an_approve_that_could_open_the_guard_too_far_or_is_malformed_refuses_the_case(approve):
    found = parse_case("approves", case_text(approve=approve))

    assert found.case is None
    assert any("approve" in problem for problem in found.problems), found.problems


def test_every_problem_of_a_file_is_listed_at_once():
    found = parse_case("broken", case_text(body="", budget={"steps": 40}, checks=[{"kind": "shell"}]))

    assert found.case is None
    assert len(found.problems) == 3


def test_unknown_tools_are_a_warning_when_the_tools_are_known():
    found = parse_case("tools", case_text(), known_tools=frozenset({"read_file", "edit_file"}))

    assert found.case is not None
    assert found.warnings == ("Unknown tools in allowed_tools: run_command.",)


def test_cases_are_read_from_the_project_blueprint_layer(tmp_path: Path):
    evals = tmp_path / ".blueprint" / "evals"
    evals.mkdir(parents=True)
    (evals / "good.md").write_text(case_text(), encoding="utf-8")
    (evals / "bad.md").write_text(case_text(body=""), encoding="utf-8")
    (evals / "notes.txt").write_text("not a case", encoding="utf-8")

    found = read_cases(tmp_path)

    assert [(case.name, case.case is not None) for case in found] == [("bad", False), ("good", True)]
    assert found[1].path == ".blueprint/evals/good.md"
    assert read_cases(tmp_path / "elsewhere") == []


@pytest.mark.parametrize(
    ("parse", "value", "expected"),
    [
        (parse_steps, "40 steps", 40),
        (parse_steps, "1 step", 1),
        (parse_tokens, "200k tokens", 200_000),
        (parse_tokens, "1.5M tokens", 1_500_000),
        (parse_tokens, "900 tokens", 900),
        (parse_cost, "0.50 USD", 0.5),
        (parse_cost, "$2", 2.0),
        (parse_seconds, "90 s", 90),
        (parse_seconds, "10 min", 600),
        (parse_seconds, "1 h", 3600),
    ],
)
def test_a_quantity_with_its_unit_is_read_in_the_base_unit(parse, value, expected):
    assert parse(value) == expected


@pytest.mark.parametrize(
    ("parse", "value"),
    [
        (parse_steps, 40),
        (parse_steps, "40"),
        (parse_steps, "2.5 steps"),
        (parse_steps, "40 min"),
        (parse_tokens, "$100 tokens"),
        (parse_tokens, "200k"),
        (parse_cost, "0.5"),
        (parse_cost, "0.5 EUR"),
        (parse_cost, "$1k"),
        (parse_seconds, "10"),
        (parse_seconds, "10M"),
        (parse_seconds, "0 s"),
        (parse_seconds, True),
        (parse_seconds, ["10 s"]),
    ],
)
def test_a_quantity_without_a_known_unit_is_refused(parse, value):
    with pytest.raises(UnitError):
        parse(value)


def test_the_example_in_the_user_documentation_is_a_valid_case():
    usage = (Path(__file__).resolve().parents[2] / "docs" / "usage.md").read_text(encoding="utf-8")
    section = usage.split("### Eval cases", 1)[1]
    example = section.split("```markdown\n", 1)[1].split("\n```", 1)[0]

    found = parse_case("iso-week", example)

    assert found.problems == () and found.case is not None
    assert len(found.case.checks) == 4 and found.case.budget == Budget(40, 200_000, 0.5, 600)
