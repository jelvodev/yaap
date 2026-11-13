"""The Current state block of the project memory: reading, replacing and checking it."""

import pytest

from app.projects.current_state import MAX_STATE_CHARS, check_state, read_state, replace_state, without_state
from app.projects.memory import TEMPLATE, add_entry, parse_entries

BLOCK = "**Where it stands:** Login works.\n\n**Open:** Password reset.\n\n**Next step:** Write the reset mail."


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", None),
        ("# Project memory\n\n## Decisions\n\n- Use SQLite.\n", None),
        ("# Project memory\n\n## Current state\n\n## Decisions\n", ""),
        ("# Project memory\n\n## Current state\n", ""),
        (f"# Project memory\n\n## Current state\n\n{BLOCK}\n\n## Decisions\n\n- Use SQLite.\n", BLOCK),
        (f"# Project memory\r\n\r\n## Current state\r\n\r\n{BLOCK.replace(chr(10), chr(13) + chr(10))}\r\n", BLOCK),
        ("## current state\n\nlowercase heading\n", "lowercase heading"),
    ],
    ids=["absent", "no_section", "empty_between_sections", "section_only", "with_entries", "crlf", "case_insensitive"],
)
def test_read_state_finds_the_block_in_each_shape_of_memory(text, expected):
    assert read_state(text) == expected


def test_a_decisions_line_inside_the_block_text_ends_the_block_at_the_next_section():
    text = f"# Project memory\n\n## Current state\n\n{BLOCK}\n- Decisions: not a heading\n\n## Decisions\n\n- A\n"

    state = read_state(text)

    assert state is not None and state.endswith("- Decisions: not a heading")
    assert "- A" not in state


def test_replace_state_adds_the_block_before_the_other_sections():
    updated = replace_state(TEMPLATE, BLOCK)

    assert updated.index("## Current state") < updated.index("## Decisions")
    assert read_state(updated) == BLOCK
    assert updated.startswith("# Project memory\n\n## Current state\n\n**Where it stands:**")


def test_replace_state_in_an_empty_memory_starts_from_the_title():
    assert replace_state("", BLOCK) == f"# Project memory\n\n## Current state\n\n{BLOCK}\n"


def test_replace_state_keeps_the_entries_around_the_block():
    text = add_entry(TEMPLATE, "Decisions", "Use SQLite.")
    first = replace_state(text, "old")

    second = replace_state(first, BLOCK)

    assert read_state(second) == BLOCK
    assert "- Use SQLite." in second and second.count("## Current state") == 1
    assert second.index("## Current state") < second.index("## Decisions")


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_replace_state_keeps_the_line_endings_of_the_file(newline):
    text = TEMPLATE.replace("\n", newline)

    updated = replace_state(replace_state(text, "one"), BLOCK)

    assert updated.count("\r\n") == updated.count("\n") if newline == "\r\n" else "\r" not in updated
    assert read_state(updated) == BLOCK


def test_replacing_with_the_same_text_again_gives_the_same_file():
    once = replace_state(add_entry(TEMPLATE, "Notes", "Ask first."), BLOCK)

    assert replace_state(once, BLOCK) == once
    assert replace_state(once, f"\n{BLOCK}\n\n") == once


def test_replace_state_in_the_last_section_of_a_file_keeps_it_well_formed():
    text = "# Project memory\n\n## Current state\n\nold\n"

    updated = replace_state(text, BLOCK)

    assert updated == f"# Project memory\n\n## Current state\n\n{BLOCK}\n"


def test_without_state_leaves_the_other_sections():
    text = replace_state(add_entry(TEMPLATE, "Decisions", "Use SQLite."), BLOCK)

    rest = without_state(text)

    assert "Current state" not in rest and BLOCK not in rest and "- Use SQLite." in rest
    assert without_state(TEMPLATE) == TEMPLATE


def test_parse_entries_skips_the_block():
    text = replace_state(add_entry(TEMPLATE, "Decisions", "Use SQLite."), "- Login works.\n- Reset is open.")

    entries = parse_entries(text)

    assert [(entry.section, entry.text) for entry in entries] == [("Decisions", "Use SQLite.")]


@pytest.mark.parametrize(
    ("new", "problem"),
    [
        (BLOCK, None),
        ("", "empty"),
        ("  \n\n ", "empty"),
        ("x" * MAX_STATE_CHARS, None),
        ("x" * (MAX_STATE_CHARS + 1), "at most"),
        (f"  {'x' * MAX_STATE_CHARS}  \n", None),
        ("**Open:** a\n## Decisions\nb", "heading"),
        ("# Title\ntext", "heading"),
        ("   ## Indented heading", "heading"),
        ("A line with a # inside it", None),
        ("#hashtag without a space", None),
    ],
    ids=[
        "valid", "empty", "blank", "at_budget", "over_budget", "padding_not_counted", "section_heading",
        "title_heading", "indented_heading", "hash_inside", "hashtag",
    ],
)
def test_check_state_refuses_what_cannot_be_the_block(new, problem):
    result = check_state(new)

    if problem is None:
        assert result is None
    else:
        assert result is not None and problem in result
