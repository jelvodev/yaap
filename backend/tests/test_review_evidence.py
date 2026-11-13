"""The evidence of a card's review: statuses and sources per criterion, the stored form, and the reviewer's answer."""

import json

import pytest

from app.evals.models import CommandCheck, FileCheck, RubricCheck
from app.plans.model import Plan, PlanStep, StepCheck
from app.plans.progress import FinalCheck
from app.reviews.evidence import (
    MAX_OUTPUT,
    MAX_REASON,
    CheckRun,
    CriterionCheck,
    DiffStat,
    Evidence,
    Judgement,
    TraceRef,
    assemble,
    awaiting_reviewer,
    diff_stat,
    evidence_from_json,
    evidence_markdown,
    evidence_to_json,
    plan_checks,
    with_judgements,
)
from app.reviews.report import ReportError, parse_judgements
from app.specs.criteria import Criterion

COMMAND = Criterion(1, "The tests pass", CommandCheck("1. The tests pass", "pytest -q", 60))
FILE = Criterion(2, "The readme exists", FileCheck("2. The readme exists", "README.md"))
PLAIN = Criterion(3, "The page is friendly", RubricCheck("3. The page is friendly", "The page is friendly"))
CRITERIA = (COMMAND, FILE, PLAIN)


def command_run(outcome, output="", number=1):
    return CriterionCheck(number, "command", "pytest -q", outcome, output)


def file_run(outcome, output="", number=2):
    return CriterionCheck(number, "file", "file: README.md exists", outcome, output)


# --- Criteria with a check of YAAP's ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("outcome", "output", "status", "has_source", "note"),
    [
        ("passed", "", "passed", True, ""),
        ("failed", "1 failed", "failed", True, ""),
        ("not_run", "", "not_checked", True, "The check did not run."),
        ("stopped", "Stopped by the user.\nmore", "not_checked", True, "Stopped by the user."),
        ("error", "Could not start", "not_checked", True, "Could not start"),
    ],
    ids=["passed", "failed", "not_run", "stopped", "error"],
)
def test_a_command_criterion_is_passed_or_failed_only_when_the_command_ran(outcome, output, status, has_source, note):
    evidence = assemble([COMMAND], [command_run(outcome, output)])

    item = evidence.criteria[0]
    assert (item.status, item.how, item.note) == (status, "command", note)
    assert (item.source is not None) is has_source
    assert item.source is not None and item.source.kind == "check_output" and item.source.ref == "check:0"


@pytest.mark.parametrize(("outcome", "status"), [("passed", "passed"), ("failed", "failed")])
def test_a_file_criterion_has_how_file_and_its_own_status(outcome, status):
    item = assemble([FILE], [file_run(outcome)]).criteria[0]

    assert (item.status, item.how) == (status, "file")


@pytest.mark.parametrize(("criterion", "how"), [(COMMAND, "command"), (FILE, "file")], ids=["command", "file"])
def test_a_check_that_did_not_run_at_all_is_not_checked_without_a_source(criterion, how):
    item = assemble([criterion], []).criteria[0]

    assert (item.status, item.how, item.source, item.note) == ("not_checked", how, None, "The check did not run.")


def test_a_plain_criterion_waits_for_the_reviewer_and_is_not_checked_until_then():
    evidence = assemble(CRITERIA, [command_run("passed"), file_run("failed")])

    assert [(item.number, item.status, item.how) for item in evidence.criteria] == [
        (1, "passed", "command"),
        (2, "failed", "file"),
        (3, "not_checked", "none"),
    ]
    assert [item.number for item in awaiting_reviewer(evidence)] == [3]


def test_every_check_gets_its_own_source_reference_after_the_earlier_checks():
    earlier = [CheckRun("make", "passed", "step 1", "step:1"), CheckRun("make all", "passed", "final", "final:0")]

    evidence = assemble([COMMAND, FILE], [command_run("passed"), file_run("failed", "missing")], earlier)

    assert [run.ref for run in evidence.checks_run] == ["step:1", "final:0", "check:2", "check:3"]
    assert [item.source.ref for item in evidence.criteria] == ["check:2", "check:3"]  # type: ignore[union-attr]
    assert evidence.checks_run[2].step == "criterion 1" and evidence.checks_run[3].output == "missing"


def test_a_long_check_output_is_cut():
    evidence = assemble([COMMAND], [command_run("failed", "x" * (MAX_OUTPUT * 3))])

    assert len(evidence.checks_run[0].output) == MAX_OUTPUT


# --- The reviewer's answers ------------------------------------------------------------------------------------------


def waiting() -> Evidence:
    return assemble(CRITERIA, [command_run("passed"), file_run("passed")])


@pytest.mark.parametrize(
    ("judgement", "status", "source_ref"),
    [
        (Judgement(3, "met", "app/page.py", 12, "It greets."), "judged_met", "app/page.py:12"),
        (Judgement(3, "met", "app/page.py", None, "It greets."), "judged_met", "app/page.py"),
        (Judgement(3, "not_met", "app/page.py", 4, "It is rude."), "judged_not_met", "app/page.py:4"),
        (Judgement(3, "unclear", "app/page.py", 4, "Cannot tell."), "not_checked", "app/page.py:4"),
        (Judgement(3, "unclear", None, None, "Cannot tell."), "not_checked", None),
        (Judgement(3, "met", None, None, "No file named."), "not_checked", None),
        (Judgement(3, "not_met", "", None, "No file named."), "not_checked", None),
    ],
    ids=["met_with_line", "met_without_line", "not_met", "unclear_with_file", "unclear", "met_without_file", "blank_path"],
)
def test_a_reviewer_answer_counts_only_with_the_file_it_relied_on(judgement, status, source_ref):
    item = with_judgements(waiting(), [judgement]).criteria[2]

    assert item.status == status and item.how == "reviewer"
    assert (item.source.ref if item.source is not None else None) == source_ref
    assert item.source is None or item.source.kind == "finding"
    assert item.note == judgement.reason


def test_a_criterion_without_a_check_and_no_reviewer_answer_stays_not_checked():
    evidence = with_judgements(waiting(), [])

    assert evidence.criteria[2].status == "not_checked" and evidence.criteria[2].how == "none"
    assert awaiting_reviewer(evidence)


def test_the_reviewer_cannot_change_a_criterion_that_yaap_checked():
    judged = with_judgements(waiting(), [Judgement(1, "not_met", "a.py", 1, "No."), Judgement(2, "not_met", "a.py", 1)])

    assert [item.status for item in judged.criteria[:2]] == ["passed", "passed"]
    assert all(item.how in ("command", "file") for item in judged.criteria[:2])


def test_a_reason_of_the_reviewer_is_cut():
    item = with_judgements(waiting(), [Judgement(3, "met", "a.py", 1, "y" * (MAX_REASON * 2))]).criteria[2]

    assert len(item.note) == MAX_REASON


def test_every_status_and_source_kind_survives_the_stored_form():
    evidence = with_judgements(
        assemble(
            [*CRITERIA, Criterion(4, "Unrun", CommandCheck("4", "make", 60))],
            [command_run("passed"), file_run("failed", "missing")],
            [CheckRun("pytest", "passed", "step 1", "step:1")],
            TraceRef(7, 9),
            ["No preview for this project"],
        ),
        [Judgement(3, "met", "app/page.py", 12, "Greets.")],
    )

    stored = json.loads(json.dumps(evidence_to_json(evidence)))

    assert evidence_from_json(stored) == evidence
    assert {item["status"] for item in stored["criteria"]} == {"passed", "failed", "judged_met", "not_checked"}


@pytest.mark.parametrize(
    "value",
    [None, [], "text", 5],
    ids=["none", "list", "text", "number"],
)
def test_evidence_that_is_not_an_object_reads_as_none(value):
    assert evidence_from_json(value) is None


def test_unknown_stored_values_fall_back_to_the_safest_reading():
    stored = {
        "criteria": [
            {"number": "x", "text": None, "status": "approved", "how": "magic", "source": {"kind": "gossip", "ref": "r"}},
            "not an object",
            {"number": 2, "status": "passed", "how": "command", "source": {"kind": "run_step", "ref": "step:1"}},
        ],
        "checks_run": [{"command": "make", "outcome": "great"}],
        "diff_stat": {"files": True, "insertions": "3", "deletions": 2},
        "trace_ref": {"conversation_id": 0},
        "screenshots": [{"label": "sideways", "ref": "s.png"}],
        "notes": ["ok", 5],
    }

    evidence = evidence_from_json(stored)

    assert evidence is not None
    first, second = evidence.criteria
    assert (first.number, first.text, first.status, first.how, first.source) == (0, "", "not_checked", "none", None)
    assert (second.status, second.source.kind) == ("passed", "run_step")  # type: ignore[union-attr]
    assert evidence.checks_run[0].outcome == "not_run"
    assert evidence.diff_stat == DiffStat(0, 0, 2) and evidence.trace_ref is None
    assert evidence.screenshots[0].label == "after" and evidence.notes == ("ok",)


# --- The plan's checks, the diff and the card's summary ---------------------------------------------------------------


def test_the_plans_step_checks_come_first_then_the_final_run():
    plan = Plan(
        "Title",
        steps=(
            PlanStep(1, "One", checks=(StepCheck("works", "pytest"), StepCheck("by eye")), status="done"),
            PlanStep(2, "Two", checks=(StepCheck("works", "make"),), status="failed"),
            PlanStep(3, "Three", checks=(StepCheck("works", "ruff"),), status="open"),
        ),
    )

    runs = plan_checks(plan, [FinalCheck("make all", "passed", "z" * (MAX_OUTPUT + 5))])

    assert [(run.command, run.outcome, run.step, run.ref) for run in runs] == [
        ("pytest", "passed", "step 1", "step:1"),
        ("make", "failed", "step 2", "step:2"),
        ("ruff", "not_run", "step 3", "step:3"),
        ("make all", "passed", "final", "final:0"),
    ]
    assert len(runs[-1].output) == MAX_OUTPUT


def test_the_diff_stat_counts_files_and_changed_lines_but_not_the_file_headers():
    diff = (
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1,2 @@\n-old\n+new\n+newer\n"
        "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n context\n-gone\n"
    )

    assert diff_stat(diff) == DiffStat(2, 2, 2)
    assert diff_stat("") == DiffStat()


def test_the_card_summary_marks_what_was_met_and_counts_the_checks():
    evidence = with_judgements(
        assemble(CRITERIA, [command_run("passed"), file_run("failed")], [CheckRun("make", "passed", "final", "final:0")],
                 notes=["No preview for this project"]),
        [Judgement(3, "met", "app/page.py", 12, "Greets.")],
    )

    text = evidence_markdown(evidence)

    assert text.splitlines()[:3] == [
        "- [x] 1. The tests pass: passed",
        "- [ ] 2. The readme exists: failed",
        "- [x] 3. The page is friendly: met, judged by the reviewer (app/page.py:12)",
    ]
    assert "Checks that ran: 3, 2 passed." in text and "Diff: 0 files, +0 -0." in text
    assert text.endswith("No preview for this project.")


def test_a_card_without_criteria_says_so():
    assert evidence_markdown(Evidence()).startswith("The card has no acceptance criteria.")


# --- The reviewer's output parser ---------------------------------------------------------------------------------------


def answer(*items: object) -> str:
    return "Here is my review.\n" + json.dumps({"criteria": list(items)}) + "\nThanks."


def test_the_parser_reads_a_well_formed_answer_inside_other_text():
    found = parse_judgements(
        answer(
            {"number": 3, "verdict": "met", "path": " app/page.py ", "line": 12, "reason": " Greets. "},
            {"number": 4, "verdict": "unclear", "path": None, "line": None, "reason": "No sign."},
        ),
        [3, 4],
    )

    assert found == [Judgement(3, "met", "app/page.py", 12, "Greets."), Judgement(4, "unclear", None, None, "No sign.")]


@pytest.mark.parametrize(
    "item",
    [
        {"number": 9, "verdict": "met", "path": "a.py"},
        {"number": 3, "verdict": "maybe", "path": "a.py"},
        {"number": 3, "path": "a.py"},
        {"verdict": "met", "path": "a.py"},
        {"number": "three", "verdict": "met"},
        "met",
        None,
        7,
    ],
    ids=["unknown_number", "unknown_verdict", "no_verdict", "no_number", "number_text", "string", "null", "int"],
)
def test_the_parser_leaves_out_what_it_cannot_use(item):
    assert parse_judgements(answer(item), [3]) == []


def test_the_first_answer_per_number_counts():
    found = parse_judgements(
        answer(
            {"number": 3, "verdict": "met", "path": "a.py", "line": 1},
            {"number": 3, "verdict": "not_met", "path": "b.py", "line": 2},
        ),
        [3],
    )

    assert [(item.verdict, item.path) for item in found] == [("met", "a.py")]


@pytest.mark.parametrize(
    ("path", "line", "expected_path", "expected_line"),
    [
        (5, 3, None, 3),
        ("", 3, None, 3),
        ("   ", 3, None, 3),
        ({"x": 1}, "12", None, 12),
        ("a.py", "x", "a.py", None),
        ("a.py", True, "a.py", None),
        ("a.py", 1.5, "a.py", None),
    ],
    ids=["number_path", "empty_path", "blank_path", "object_path", "bad_line", "bool_line", "float_line"],
)
def test_the_parser_reads_a_path_and_a_line_leniently(path, line, expected_path, expected_line):
    found = parse_judgements(answer({"number": 3, "verdict": "met", "path": path, "line": line}), [3])

    assert [(item.path, item.line) for item in found] == [(expected_path, expected_line)]


def test_a_long_reason_is_cut_and_a_reason_that_is_not_text_is_empty():
    found = parse_judgements(
        answer(
            {"number": 3, "verdict": "met", "path": "a.py", "reason": "r" * 5000},
            {"number": 4, "verdict": "met", "path": "a.py", "reason": 12},
        ),
        [3, 4],
    )

    assert 0 < len(found[0].reason) < 5000 and found[1].reason == ""


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "holds no report"),
        ("I think everything is fine.", "holds no report"),
        ('{"criteria": [{"number": 3, "verdict": "met"', "holds no report|not valid JSON"),
        ("{criteria: nope}", "not valid JSON"),
        ('{"criteria": "all good"}', "no list of criteria"),
        ('{"findings": []}', "no list of criteria"),
        ('{"criteria": null}', "no list of criteria"),
    ],
    ids=["empty", "prose", "truncated", "not_json", "criteria_text", "other_key", "criteria_null"],
)
def test_the_parser_refuses_an_answer_that_holds_no_usable_report(text, message):
    with pytest.raises(ReportError, match=message):
        parse_judgements(text, [3])
