"""Reading a worker's report and building what the lead gets."""

import pytest

from app.agent.sub_run_report import (
    MAX_CHECK_OUTPUT,
    MAX_DIFF,
    MAX_REPORT,
    CheckResult,
    SubRunReport,
    capped,
    files_diff,
    lead_report,
    parse_report,
)

PLAIN = "Status: done\nConclusion: Fixed the bug.\nChanged files: a.py, b.py\nLeft open: nothing"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (PLAIN, SubRunReport("done", "Fixed the bug.", ("a.py", "b.py"), "nothing", True)),
        (
            "**Status:** done\n**Conclusion:** Fixed.\n**Changed files:** `a.py`\n**Left open:** none",
            SubRunReport("done", "Fixed.", ("a.py",), "none", True),
        ),
        (
            "- Status: Blocked\n- Conclusion: No access.\n- Changed files: none\n- Left open: credentials",
            SubRunReport("blocked", "No access.", (), "credentials", True),
        ),
        (
            "status: FAILED - tests\nconclusion: x\nchanged files:\n- a.py\n- `dir/b.py`\n",
            SubRunReport("failed", "x", ("a.py", "dir/b.py"), "", True),
        ),
        (
            "Some chatter first.\nStatus: done\nConclusion: Line one.\nLine two.\n\nChanged files: -\nLeft open: x",
            SubRunReport("done", "Line one.\nLine two.", (), "x", True),
        ),
        ("Status: maybe\nConclusion: unsure", SubRunReport(None, "unsure", (), "", True)),
        ("Status: done", SubRunReport("done", "", (), "", True)),
        ("I fixed it.\nAll good.\n", SubRunReport(None, "I fixed it.\nAll good.", (), "", False)),
        ("", SubRunReport(None, "", (), "", False)),
        ("Conclusion: only this", SubRunReport(None, "Conclusion: only this", (), "", False)),
    ],
)
def test_report_is_read_leniently_and_free_text_is_kept(text: str, expected: SubRunReport) -> None:
    assert parse_report(text) == expected


@pytest.mark.parametrize(
    ("text", "limit", "expected"),
    [
        ("short", 10, "short"),
        ("exactly10!", 10, "exactly10!"),
        ("abcdefghijkl", 10, "abcdefghij\n\n[Cut at 10 characters; 2 more were left out.]"),
        ("abcd     efgh", 8, "abcd\n\n[Cut at 8 characters; 9 more were left out.]"),
        ("x" * 2000 + "y" * 1000, 2000, "x" * 2000 + "\n\n[Cut at 2,000 characters; 1,000 more were left out.]"),
    ],
)
def test_capped_cuts_with_a_note_and_leaves_short_text_alone(text: str, limit: int, expected: str) -> None:
    assert capped(text, limit) == expected


DIFF = (
    "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n+y\n"
    "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n-1\n+2\n"
    "diff --git a/old.py b/new.py\nsimilarity index 90%\nrename from old.py\nrename to new.py\n"
)
A_SECTION = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n+y\n"
B_SECTION = "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n-1\n+2\n"
RENAME_SECTION = "diff --git a/old.py b/new.py\nsimilarity index 90%\nrename from old.py\nrename to new.py\n"


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        (["a.py"], A_SECTION),
        (["b.py"], B_SECTION),
        (["b.py", "a.py"], A_SECTION + B_SECTION),
        (["new.py"], RENAME_SECTION),
        (["old.py"], RENAME_SECTION),
        (["c.py"], ""),
        ([], ""),
        (["a.p"], ""),
    ],
)
def test_files_diff_keeps_only_the_sections_of_the_given_paths(paths: list[str], expected: str) -> None:
    assert files_diff(DIFF, paths) == expected


def test_files_diff_of_an_empty_diff_is_empty() -> None:
    assert files_diff("", ["a.py"]) == ""


def test_lead_report_for_a_structured_report_lists_check_and_diff() -> None:
    report = parse_report(PLAIN)
    check = CheckResult("pytest -q", "passed", "Exit code 0\n3 passed")
    text = lead_report(report, PLAIN, check, DIFF, ["a.py"])
    assert text == (
        "Status: done\nConclusion: Fixed the bug.\nLeft open: nothing\n\n"
        "Changed files:\n- a.py\n\n"
        "YAAP ran the check `pytest -q`: passed.\nExit code 0\n3 passed\n\n"
        f"Diff of the changed files:\n{A_SECTION}"
    )


@pytest.mark.parametrize(
    ("outcome", "words"),
    [("passed", "passed."), ("failed", "failed."), ("not_run", "could not run.")],
)
def test_lead_report_names_the_check_outcome(outcome: str, words: str) -> None:
    check = CheckResult("make test", outcome, "")  # type: ignore[arg-type]  # str from the table
    text = lead_report(parse_report(PLAIN), PLAIN, check, "", [])
    assert f"YAAP ran the check `make test`: {words}" in text
    assert "Changed files:\n- none" in text
    assert "Diff of the changed files" not in text


def test_lead_report_without_a_check_says_so_and_keeps_free_text() -> None:
    raw = "  I did it.  "
    text = lead_report(parse_report(raw), raw, None, "", ["a.py", "b.py"])
    assert text == "I did it.\n\nChanged files:\n- a.py\n- b.py\n\nNo check was given."


def test_lead_report_marks_an_unknown_status() -> None:
    raw = "Status: maybe\nConclusion: c"
    assert lead_report(parse_report(raw), raw, None, "", []).startswith("Status: unknown\nConclusion: c")


def test_lead_report_caps_report_check_output_and_diff() -> None:
    raw = "Status: done\nConclusion: " + "c" * (MAX_REPORT + 50)
    check = CheckResult("t", "failed", "o" * (MAX_CHECK_OUTPUT + 50))
    big = "diff --git a/a.py b/a.py\n" + "+" + "d" * (MAX_DIFF + 50) + "\n"
    text = lead_report(parse_report(raw), raw, check, big, ["a.py"])
    assert f"[Cut at {MAX_REPORT:,} characters;" in text
    assert f"[Cut at {MAX_CHECK_OUTPUT:,} characters;" in text
    assert f"[Cut at {MAX_DIFF:,} characters;" in text
    assert text.count("c") < MAX_REPORT + 50
    assert text.count("o") < MAX_CHECK_OUTPUT + 50
