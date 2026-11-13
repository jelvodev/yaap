"""Version files: read like cards, never raising on content, written deterministically."""

from datetime import UTC, datetime

import pytest

from app.board.errors import InvalidCard
from app.board.paths import MAX_FILE_BYTES
from app.board.versions import BrokenVersion, Version, parse_version, render_version, validate_version

PATH = "versions/0.8.0.md"
WHEN = "2026-11-01T10:00:00Z"


def version_text(body: str = "", **overrides: object) -> str:
    fields: dict[str, object] = {"name": "0.8.0", "created": WHEN}
    fields.update(overrides)
    lines = "".join(f"{key}: {value}\n" for key, value in fields.items() if value is not None)
    return f"---\n{lines}---\n{body}"


def parsed(text: str, path: str = PATH) -> Version:
    result = parse_version(text, path)
    assert isinstance(result, Version), result
    return result


def test_a_minimal_version_is_open_without_a_budget() -> None:
    version = parsed(version_text())
    assert (version.name, version.state, version.budget_usd) == ("0.8.0", "open", None)
    assert version.created == datetime(2026, 11, 1, 10, tzinfo=UTC)


def test_notes_and_acceptance_criteria_are_split() -> None:
    version = parsed(version_text("Notes here\n\n## Acceptance criteria\n\n- [ ] `pytest`\n", budget_usd="12.345"))
    assert (version.notes, version.acceptance_criteria, version.budget_usd) == ("Notes here", "- [ ] `pytest`", 12.35)


def test_a_criteria_heading_inside_a_fence_stays_in_the_notes() -> None:
    version = parsed(version_text("```\n## Acceptance criteria\n```\n"))
    assert version.acceptance_criteria == "" and "## Acceptance criteria" in version.notes


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"state": "closed"}, "state"),
        ({"budget_usd": "-1"}, "budget_usd"),
        ({"budget_usd": "'5'"}, "budget_usd"),
        ({"budget_usd": "true"}, "budget_usd"),
        ({"created": "never"}, "created"),
        ({"created": None}, "created"),
    ],
)
def test_a_bad_field_makes_a_broken_version(overrides, message) -> None:
    result = parse_version(version_text(**overrides), PATH)
    assert isinstance(result, BrokenVersion)
    assert result.name == "0.8.0" and message in result.error


@pytest.mark.parametrize(
    "text",
    ["", "plain", "---\nname: [oops\n---\n", "---\n- list\n---\n", version_text(name="0.9.0")],
)
def test_unreadable_text_or_a_name_mismatch_is_broken_not_raised(text) -> None:
    result = parse_version(text, PATH)
    assert isinstance(result, BrokenVersion) and result.error


@pytest.mark.parametrize("path", ["versions/.md", "versions/-x.md", "cards/1.md", "versions/a b.md"])
def test_a_path_that_is_not_a_version_name_is_broken_without_a_name(path) -> None:
    result = parse_version(version_text(), path)
    assert isinstance(result, BrokenVersion) and result.name is None


def test_a_version_file_over_the_cap_is_broken_without_text() -> None:
    result = parse_version(version_text("x" * (MAX_FILE_BYTES + 1)), PATH)
    assert isinstance(result, BrokenVersion) and result.text is None


def test_crlf_files_are_read_like_lf_files() -> None:
    text = version_text("Notes\n\n## Acceptance criteria\n\n- [ ] a\n", state="released")
    assert parsed(text.replace("\n", "\r\n")) == parsed(text)


@pytest.mark.parametrize(
    "body",
    ["", "Notes only\n", "Notes\n\n## Acceptance criteria\n\n- [ ] a\n", "## Acceptance criteria\n\n- [x] only\n"],
)
def test_render_is_byte_stable_with_unknown_keys_sorted(body) -> None:
    first = render_version(parsed(version_text(body, zeta="1", alpha="2", budget_usd="3.5")))
    assert first.index("alpha") < first.index("zeta")
    assert "\r" not in first
    assert render_version(parsed(first)) == first
    assert first.startswith("---\nname: 0.8.0\nstate: open\nbudget_usd: 3.5\ncreated: 2026-11-01T10:00:00Z\n")


def test_validate_version_refuses_a_bad_name_and_a_repeated_field_in_extra() -> None:
    with pytest.raises(InvalidCard):
        validate_version({"name": "-bad", "created": WHEN})
    with pytest.raises(InvalidCard):
        validate_version({"name": "1.0", "created": WHEN, "extra": {"state": "open"}})


def test_notes_cannot_hold_the_criteria_heading() -> None:
    with pytest.raises(InvalidCard):
        validate_version({"name": "1.0", "created": WHEN, "notes": "a\n## Acceptance criteria\nb"})


def test_writing_a_version_over_the_cap_is_refused() -> None:
    version = validate_version({"name": "1.0", "created": WHEN, "notes": "é" * 50_000, "acceptance_criteria": "é" * 50_000})
    with pytest.raises(InvalidCard):
        render_version(version)
