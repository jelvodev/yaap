"""Parsing and wording of a delegation brief."""

import pytest

from app.agent.brief import (
    MAX_CHECK,
    MAX_DONE_WHEN,
    MAX_FACTS,
    MAX_FILES,
    Brief,
    BriefError,
    FileRef,
    brief_message,
    parse_brief,
    parse_file_ref,
    retry_message,
    takeover_message,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("src/a.py", FileRef("src/a.py")),
        ("src/a.py:12", FileRef("src/a.py", 12)),
        ("src/a.py:12-40", FileRef("src/a.py", 12, 40)),
        ("  src/a.py:3  ", FileRef("src/a.py", 3)),
        ("src/a.py:5-5", FileRef("src/a.py", 5, 5)),
        ("dir:name/a.py", FileRef("dir:name/a.py")),
        ("dir:name/a.py:7", FileRef("dir:name/a.py", 7)),
        ("a.py:12x", FileRef("a.py:12x")),
    ],
)
def test_file_reference_is_split_on_the_last_colon_only_before_line_numbers(value: str, expected: FileRef) -> None:
    assert parse_file_ref(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "/etc/passwd",
        "\\share\\a.py",
        "C:\\dir\\a.py",
        "C:/dir/a.py",
        "C:",
        "a\nb.py",
        "a\0b.py",
        ":12",
        "a.py:0",
        "a.py:0-5",
        "a.py:9-3",
    ],
)
def test_file_reference_rejects_unusable_values(value: str) -> None:
    with pytest.raises(BriefError):
        parse_file_ref(value)


@pytest.mark.parametrize(
    ("ref", "label"),
    [
        (FileRef("a.py"), "a.py"),
        (FileRef("a.py", 12), "a.py:12"),
        (FileRef("a.py", 12, 12), "a.py:12"),
        (FileRef("a.py", 12, 40), "a.py:12-40"),
    ],
)
def test_file_reference_label_round_trips_the_written_form(ref: FileRef, label: str) -> None:
    assert ref.label() == label
    assert parse_file_ref(label).label() == label


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ({"task": "Fix it"}, Brief("Fix it")),
        ({"task": "  Fix it  "}, Brief("Fix it")),
        ({"task": "T", "files": None, "facts": None, "done_when": None, "check": None}, Brief("T")),
        ({"task": "T", "files": ["a.py:1-2", "b.py"]}, Brief("T", (FileRef("a.py", 1, 2), FileRef("b.py")))),
        ({"task": "T", "files": []}, Brief("T")),
        ({"task": "T", "facts": "  known  "}, Brief("T", facts="known")),
        ({"task": "T", "done_when": " ok "}, Brief("T", done_when="ok")),
        ({"task": "T", "check": " pytest -q "}, Brief("T", check="pytest -q")),
        ({"task": "T", "check": "   "}, Brief("T")),
        ({"task": "T", "check": ""}, Brief("T")),
        ({"task": "T", "mode": "work", "role": "worker", "agent": "x", "worker": "y"}, Brief("T")),
        ({"task": "T", "facts": "x" * MAX_FACTS}, Brief("T", facts="x" * MAX_FACTS)),
        ({"task": "T", "files": ["a.py"] * MAX_FILES}, Brief("T", (FileRef("a.py"),) * MAX_FILES)),
    ],
)
def test_valid_arguments_become_a_brief(arguments: dict[str, object], expected: Brief) -> None:
    assert parse_brief(arguments) == expected


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"task": None},
        {"task": ""},
        {"task": "   "},
        {"task": 5},
        {"task": "T", "files": "a.py"},
        {"task": "T", "files": [1]},
        {"task": "T", "files": ["a.py", None]},
        {"task": "T", "files": {"a.py": 1}},
        {"task": "T", "files": ["a.py"] * (MAX_FILES + 1)},
        {"task": "T", "files": ["/abs/a.py"]},
        {"task": "T", "facts": 3},
        {"task": "T", "facts": "x" * (MAX_FACTS + 1)},
        {"task": "T", "done_when": ["a"]},
        {"task": "T", "done_when": "x" * (MAX_DONE_WHEN + 1)},
        {"task": "T", "check": 1},
        {"task": "T", "check": "x" * (MAX_CHECK + 1)},
        {"task": "T", "check": "pytest\nrm -rf ."},
        {"task": "T", "check": "pytest\0"},
    ],
)
def test_invalid_arguments_are_rejected_with_a_message(arguments: dict[str, object]) -> None:
    with pytest.raises(BriefError) as error:
        parse_brief(arguments)
    assert str(error.value)


@pytest.mark.parametrize("task", ["Fix it", "  padded\n\ttask  ", "line one\n\nline two", ""])
def test_message_without_a_brief_is_the_task_unchanged(task: str) -> None:
    brief = Brief(task)
    assert not brief.has_brief()
    assert brief_message(brief) == task


@pytest.mark.parametrize(
    "brief",
    [
        Brief("T", files=(FileRef("a.py"),)),
        Brief("T", facts="f"),
        Brief("T", done_when="d"),
        Brief("T", check="c"),
    ],
)
def test_any_extra_field_makes_it_a_brief(brief: Brief) -> None:
    assert brief.has_brief()


def test_message_lists_only_the_sections_that_are_present() -> None:
    brief = Brief("Do T", (FileRef("a.py", 1, 2), FileRef("b.py")), "It is old.", "Tests pass.", "pytest -q")
    assert brief_message(brief) == (
        "Do T\n\n"
        "Files to start from:\n- a.py:1-2\n- b.py\n\n"
        "What is already known:\nIt is old.\n\n"
        "Done when:\nTests pass.\n\n"
        "Check (YAAP runs it after you finish; your own claim that it passes is not enough):\n`pytest -q`"
    )
    assert brief_message(Brief("Do T", facts="F")) == "Do T\n\nWhat is already known:\nF"
    assert brief_message(Brief("Do T", check="x")).endswith("\n`x`")
    assert "Files to start from" not in brief_message(Brief("Do T", done_when="d"))


def test_retry_message_carries_the_failure_and_the_previous_report() -> None:
    brief = Brief("Do T", check="pytest")
    message = retry_message(brief, "Exit code 1\n1 failed", "Status: done")
    assert message.startswith(brief_message(brief))
    assert "did not pass the check" in message
    assert "Exit code 1\n1 failed" in message
    assert "Status: done" in message
    assert message.endswith("report again.")


@pytest.mark.parametrize(("diff", "shown"), [("diff --git a/x b/x\n+1", "diff --git a/x b/x\n+1"), ("", "no diff")])
def test_takeover_message_carries_report_diff_and_check_output(diff: str, shown: str) -> None:
    brief = Brief("Do T", check="pytest")
    message = takeover_message(brief, "I gave up", diff, "Exit code 2")
    assert message.startswith(brief_message(brief))
    assert "tried this twice" in message
    assert "I gave up" in message
    assert shown in message
    assert "Exit code 2" in message
