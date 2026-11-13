"""The acceptance criteria of a specification, and the built-in specification skill with its templates."""

from pathlib import Path

import pytest

from app.blueprint.builtin import builtin_items, instructions, overridden_by
from app.blueprint.layers import Layer
from app.evals.models import CommandCheck, FileCheck, RubricCheck
from app.specs.criteria import MAX_CRITERIA, parse_criteria
from app.specs.templates import SPEC_KINDS, shipped_template, specification_instructions, template


def spec(*lines: str, heading: str = "### Acceptance criteria") -> str:
    return "\n".join(["## Goal", "", "Do it.", "", heading, "", *lines, "", "## Out of scope", "", "- [ ] not a criterion"])


def test_a_plain_criterion_is_judged_by_the_reviewer_with_itself_as_the_rubric():
    found = parse_criteria(spec("- [ ] The page greets the visitor by name"))

    assert len(found) == 1 and not isinstance(found[0], str)
    criterion = found[0]
    assert (criterion.number, criterion.text) == (1, "The page greets the visitor by name")
    assert isinstance(criterion.check, RubricCheck)
    assert criterion.check.rubric == "The page greets the visitor by name"


def test_a_run_criterion_is_a_command_check():
    found = parse_criteria(spec("- [ ] The tests pass `run: pytest -q tests/test_page.py`"))

    criterion = found[0]
    assert not isinstance(criterion, str) and criterion.text == "The tests pass"
    assert isinstance(criterion.check, CommandCheck)
    assert criterion.check.run == "pytest -q tests/test_page.py"


def test_a_file_contains_criterion_is_a_file_check_with_the_text():
    found = parse_criteria(spec("- [ ] The readme names the command `file: README.md contains npm run dev`"))

    check = found[0].check  # type: ignore[union-attr]  # parse succeeded, so these are criteria
    assert isinstance(check, FileCheck)
    assert check.contains == ("npm run dev",) and check.path.endswith("README.md")


def test_a_file_exists_criterion_is_a_file_check_without_text():
    found = parse_criteria(spec("- [ ] There is a changelog `file: docs/CHANGELOG.md exists`"))

    check = found[0].check  # type: ignore[union-attr]  # parse succeeded, so these are criteria
    assert isinstance(check, FileCheck) and not check.contains and check.path.endswith("docs/CHANGELOG.md")


def test_criteria_are_numbered_in_order_whatever_their_kind():
    found = parse_criteria(
        spec("- [ ] Plain", "- [x] Command `run: make test`", "- [ ] File `file: a.txt exists`", "* [ ] Star bullet")
    )

    assert [item.number for item in found] == [1, 2, 3, 4]  # type: ignore[union-attr]
    assert [type(item.check) for item in found] == [  # type: ignore[union-attr]
        RubricCheck, CommandCheck, FileCheck, RubricCheck
    ]


@pytest.mark.parametrize(
    ("line", "problem"),
    [
        ("- [ ] Two lines `run: pytest\n  && ruff check`", "one line"),
        ("- [ ] Nothing to run `run:`", "needs the command"),
        ("- [ ] Absolute `file: /etc/passwd exists`", "path"),
        ("- [ ] Windows absolute `file: C:\\Windows\\win.ini exists`", "path"),
        ("- [ ] Parent `file: ../secret.txt exists`", "path"),
        ("- [ ] Nested parent `file: docs/../../secret.txt exists`", "path"),
        ("- [ ] Bad shape `file: a.txt has`", "write the file check"),
    ],
    ids=["two_line_command", "empty_command", "absolute", "windows_absolute", "dotdot", "nested_dotdot", "bad_file_form"],
)
def test_a_broken_criterion_gives_a_list_of_problems(line, problem):
    found = parse_criteria(spec(line))

    assert found and all(isinstance(item, str) for item in found)
    assert problem in " ".join(found)  # type: ignore[arg-type]


def test_every_problem_is_collected_in_one_look():
    found = parse_criteria(spec("- [ ] One `file: ../a exists`", "- [ ] Fine", "- [ ] Two `run:`"))

    assert len(found) == 2 and all(isinstance(item, str) for item in found)
    assert "Criterion 1" in found[0] and "Criterion 3" in found[1]  # type: ignore[operator]


def test_thirty_criteria_are_allowed_and_thirty_one_are_refused():
    ok = parse_criteria(spec(*[f"- [ ] Criterion {n}" for n in range(MAX_CRITERIA)]))
    too_many = parse_criteria(spec(*[f"- [ ] Criterion {n}" for n in range(MAX_CRITERIA + 1)]))

    assert len(ok) == MAX_CRITERIA and not any(isinstance(item, str) for item in ok)
    assert len(too_many) == 1 and isinstance(too_many[0], str) and "at most 30" in too_many[0]


@pytest.mark.parametrize("fence", ["```", "~~~", "````"])
def test_a_criteria_list_inside_a_fence_is_ignored(fence):
    markdown = f"## Goal\n\nExample:\n\n{fence}markdown\n### Acceptance criteria\n\n- [ ] Only an example\n{fence}\n"

    assert parse_criteria(markdown) == []


def test_a_fenced_example_inside_the_section_is_skipped_and_the_real_items_count():
    markdown = "### Acceptance criteria\n\n- [ ] Real one\n\n```\n- [ ] Example `run: rm -rf /`\n```\n\n- [ ] Real two\n"

    found = parse_criteria(markdown)

    assert [item.text for item in found] == ["Real one", "Real two"]  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "markdown",
    ["", "## Goal\n\nNo criteria here.\n", "### Acceptance criteria\n\n", "### Acceptance criteria\n\nJust prose.\n"],
    ids=["empty", "no_section", "empty_section", "prose_only"],
)
def test_no_criteria_give_an_empty_list(markdown):
    assert parse_criteria(markdown) == []


def test_only_checkbox_items_of_the_section_count():
    found = parse_criteria(spec("- A plain bullet", "- [ ] The only criterion", "1. A numbered item"))

    assert [item.text for item in found] == ["The only criterion"]  # type: ignore[union-attr]


def test_a_continuation_line_joins_the_criterion_text():
    found = parse_criteria(spec("- [ ] The list shows all", "  open orders `run: pytest -q`"))

    criterion = found[0]
    assert not isinstance(criterion, str) and criterion.text == "The list shows all open orders"
    assert isinstance(criterion.check, CommandCheck)


def test_a_code_span_that_is_not_a_check_stays_in_the_text():
    found = parse_criteria(spec("- [ ] The `title` field is required"))

    criterion = found[0]
    assert not isinstance(criterion, str) and isinstance(criterion.check, RubricCheck)
    assert criterion.text == "The `title` field is required"


# --- The built-in skill and its templates ----------------------------------------------------------------------------


def make_layer(tmp_path: Path, kind: str, label: str) -> Layer:
    root = tmp_path / label
    return Layer(kind, label, root, root)  # type: ignore[arg-type]  # kind is one of the layer kinds


def put(layer: Layer, path: str, text: str) -> None:
    file = layer.root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text, encoding="utf-8")


def test_the_specification_skill_is_a_built_in_skill_that_says_when_to_use_it():
    item = builtin_items()["specification"]

    assert item.kind == "skill" and item.body.strip() and item.description and item.when


@pytest.mark.parametrize("kind", SPEC_KINDS)
def test_every_shipped_template_has_the_sections_and_valid_criteria(kind):
    text = shipped_template(kind)

    for heading in ("## Goal", "## Context", "### Acceptance criteria", "## Out of scope", "## Open questions"):
        assert heading in text
    found = parse_criteria(text)
    assert found and not any(isinstance(item, str) for item in found)


@pytest.mark.parametrize(
    ("kind", "headings"),
    [
        ("feature", []),
        ("chore", []),
        ("bug", ["### Steps to reproduce", "### Expected", "### Actual"]),
        ("refactor", ["What must not change"]),
    ],
)
def test_the_bug_and_refactor_templates_add_their_own_sections(kind, headings):
    text = shipped_template(kind)

    assert all(heading in text for heading in headings)


def test_without_an_override_the_shipped_template_and_instructions_are_used():
    assert template("bug", []) == shipped_template("bug")
    text = specification_instructions("bug", [])
    assert text.startswith(instructions("specification", [])) and "## Template (bug)" in text
    assert text.endswith(shipped_template("bug"))


def test_a_layer_template_wins_over_the_shipped_one_and_the_nearest_layer_wins(tmp_path):
    far, near = make_layer(tmp_path, "global", "far"), make_layer(tmp_path, "project", "near")
    put(far, "skills/specification/templates/bug.md", "Far bug template.\n")
    put(near, "skills/specification/templates/bug.md", "Near bug template.\n")

    assert template("bug", [far]) == "Far bug template."
    assert template("bug", [far, near]) == "Near bug template."
    assert template("feature", [far, near]) == shipped_template("feature")


@pytest.mark.parametrize("content", ["", "   \n"], ids=["empty", "blank"])
def test_an_empty_layer_template_falls_back_to_the_shipped_one(tmp_path, content):
    layer = make_layer(tmp_path, "global", "own")
    put(layer, "skills/specification/templates/chore.md", content)

    assert template("chore", [layer]) == shipped_template("chore")


def test_a_layer_skill_file_overrides_the_instructions(tmp_path):
    layer = make_layer(tmp_path, "global", "own")
    put(layer, "skills/specification/SKILL.md", "---\ndescription: Mine.\n---\n\nAsk three questions.\n")

    assert instructions("specification", [layer]) == "Ask three questions."
    assert overridden_by("specification", [layer]) == layer
    assert specification_instructions("feature", [layer]).startswith("Ask three questions.")
