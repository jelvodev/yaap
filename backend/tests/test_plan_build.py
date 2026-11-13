"""What a build makes of a plan: the brief per step, the branch name, the check joining and the commit message."""

import subprocess

import pytest

from app.agent.brief import MAX_CHECK, MAX_DONE_WHEN, MAX_FACTS, MAX_FILES
from app.plans.build import (
    MAX_SLUG,
    brief_check,
    build_branch_name,
    numbered_branch,
    remaining_checks,
    slug,
    step_brief,
    step_commit_message,
    step_facts,
)
from app.plans.model import Plan, PlanStep, StepCheck


def step_with(*checks: StepCheck, **changes) -> PlanStep:
    return PlanStep(changes.pop("number", 1), changes.pop("title", "Add the route"), checks=checks, **changes)


@pytest.mark.parametrize(
    ("commands", "check", "remaining"),
    [
        ((), None, ()),
        (("pytest -q",), "pytest -q", ()),
        (("pytest -q", "mypy ."), "pytest -q && mypy .", ()),
        (("a" * 300, "b" * 300), "a" * 300, ("b" * 300,)),
        (("a" * 300, "b" * 300, "c"), "a" * 300, ("b" * 300, "c")),
        (("a" * 248, "b" * 248), "a" * 248 + " && " + "b" * 248, ()),
        (("a" * 249, "b" * 248), "a" * 249, ("b" * 248,)),
    ],
)
def test_checks_with_a_command_are_joined_when_they_fit_else_the_first_runs_in_the_brief(commands, check, remaining):
    step = step_with(*(StepCheck("text", command) for command in commands), StepCheck("only a person can tell"))
    assert brief_check(step) == check
    assert remaining_checks(step) == remaining
    assert check is None or len(check) <= MAX_CHECK


def test_a_step_brief_holds_the_task_files_done_when_and_check():
    plan = Plan("Health endpoint", "Add /health.")
    step = step_with(
        StepCheck("It answers", "curl -f localhost/health"),
        StepCheck("Docs mention it"),
        details="Return ok.",
        files=("app.py", "docs/api.md"),
    )
    brief = step_brief(plan, step, [])
    assert brief.task == "Add the route\n\nReturn ok."
    assert [ref.path for ref in brief.files] == ["app.py", "docs/api.md"]
    assert brief.done_when == "- It answers\n- Docs mention it"
    assert brief.check == "curl -f localhost/health"
    assert "Add /health." in brief.facts


def test_a_step_without_details_has_its_title_as_the_task_and_without_a_command_no_check():
    brief = step_brief(Plan("T"), step_with(StepCheck("looks right")), [])
    assert brief.task == "Add the route" and brief.check is None


def test_a_step_brief_caps_files_done_when_and_facts():
    plan = Plan("T", "s" * 6000)
    step = step_with(
        *(StepCheck("c" * 290) for _ in range(10)), files=tuple(f"f{index}.py" for index in range(MAX_FILES + 5))
    )
    brief = step_brief(plan, step, [(1, "n" * 3000)])
    assert len(brief.files) == MAX_FILES
    assert len(brief.done_when) <= MAX_DONE_WHEN
    assert len(brief.facts) <= MAX_FACTS


def test_facts_name_the_plan_the_criteria_and_the_notes_of_finished_steps():
    facts = step_facts(Plan("Health", "Add /health."), [(1, "Made the route."), (2, "Added\n  a test.")], "- It works")
    assert facts.startswith("The plan: Health\nAdd /health.")
    assert "Acceptance criteria of the card:\n- It works" in facts
    assert "Done so far:\n- Step 1: Made the route.\n- Step 2: Added a test." in facts


def test_facts_without_a_summary_or_notes_are_only_the_plan_title():
    assert step_facts(Plan("Health"), []) == "The plan: Health"


def test_when_the_notes_do_not_all_fit_the_newest_are_kept():
    notes = [(number, f"{number}" * 590) for number in range(1, 9)]
    facts = step_facts(Plan("T"), notes)
    assert len(facts) <= MAX_FACTS
    assert "- Step 8:" in facts and "- Step 1:" not in facts


@pytest.mark.parametrize(
    ("plan_id", "card_id", "title", "expected"),
    [
        (7, None, "Add a health endpoint", "yaap/plan-7-add-a-health-endpoint"),
        (7, 21, "Add a health endpoint", "yaap/21-add-a-health-endpoint"),
        (7, None, "  Fix: the (weird) NAME!! ", "yaap/plan-7-fix-the-weird-name"),
        (7, None, "!!!", "yaap/plan-7-plan"),
        (7, None, "Ünïcode only", "yaap/plan-7-n-code-only"),
        (7, None, "x" * 100, "yaap/plan-7-" + "x" * MAX_SLUG),
        (7, None, "word " * 20, "yaap/plan-7-" + ("word-" * 6).rstrip("-")),
    ],
)
def test_build_branch_names_are_short_slugs_git_accepts(plan_id, card_id, title, expected):
    name = build_branch_name(plan_id, card_id, title)
    assert name == expected
    assert subprocess.run(["git", "check-ref-format", "--branch", name], capture_output=True).returncode == 0


def test_slug_never_ends_in_a_dash_after_the_cut():
    assert not slug("a" * 29 + " bbbb").endswith("-")


@pytest.mark.parametrize(
    ("taken", "expected"),
    [([], "yaap/x"), (["other"], "yaap/x"), (["yaap/x"], "yaap/x-2"), (["yaap/x", "yaap/x-2", "yaap/x-3"], "yaap/x-4")],
)
def test_a_branch_name_that_exists_gets_the_next_number(taken, expected):
    assert numbered_branch("yaap/x", taken) == expected


@pytest.mark.parametrize(
    ("number", "title", "expected"),
    [(1, "Add the route", "Step 1: Add the route"), (12, "Two\nlines  here", "Step 12: Two lines here")],
)
def test_the_step_commit_message_is_one_line(number, title, expected):
    assert step_commit_message(PlanStep(number, title)) == expected


def test_a_very_long_step_title_is_cut_in_the_commit_message():
    message = step_commit_message(PlanStep(3, "t" * 500))
    assert len(message) <= 200 and message.startswith("Step 3: ")
