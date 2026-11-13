"""Card files: reading never raises on content, bad cards become broken cards, and writing is byte-stable."""

from datetime import UTC, datetime

import pytest

from app.board.card import (
    COLUMNS,
    BrokenCard,
    Card,
    ChecklistItem,
    join_sections,
    parse_card,
    parse_checklist,
    render_card,
    revise,
    split_sections,
    validate_card,
)
from app.board.errors import InvalidCard
from app.board.paths import MAX_FILE_BYTES

PATH = "cards/7.md"
WHEN = "2026-11-01T10:00:00Z"
KEY_ORDER = [
    "id",
    "title",
    "kind",
    "column",
    "version",
    "origin",
    "waits_for",
    "questions",
    "cost_usd",
    "links",
    "created",
    "updated",
]


def front(**overrides: object) -> str:
    """Front matter lines of a valid card, with some keys replaced (a value of None drops the key)."""
    fields: dict[str, object] = {"id": 7, "title": "Add search", "created": WHEN, "updated": WHEN}
    fields.update(overrides)
    return "".join(f"{key}: {value}\n" for key, value in fields.items() if value is not None)


def card_text(body: str = "", **overrides: object) -> str:
    return f"---\n{front(**overrides)}---\n{body}"


def parsed(text: str, path: str = PATH) -> Card:
    result = parse_card(text, path)
    assert isinstance(result, Card), result
    return result


def test_a_minimal_card_gets_the_defaults() -> None:
    card = parsed(card_text())
    assert (card.id, card.title, card.kind, card.column, card.version) == (7, "Add search", "feature", "inbox", None)
    assert card.origin.type == "user" and card.waits_for == [] and card.cost_usd == 0.0
    assert card.created == datetime(2026, 11, 1, 10, tzinfo=UTC)


def test_a_full_card_is_read_field_by_field() -> None:
    text = card_text(
        "Intro\n\n## Definition of done\n\n- [ ] one\n\n## Plan\n\nDo it\n",
        kind="bug",
        column="plan",
        version="0.8.0",
        origin="{type: chat, ref: conv-3}",
        waits_for="[1, 2]",
        cost_usd="1.239",
        links="{conversations: [4], plan_id: 9, branch: card-7}",
        questions="[{asked: 2026-11-02T08:00:00Z, text: 'Why?', answer: null}]",
    )
    card = parsed(text)
    assert (card.kind, card.column, card.version) == ("bug", "plan", "0.8.0")
    assert card.waits_for == [1, 2] and card.cost_usd == 1.24
    assert card.origin.type == "chat" and card.origin.ref == "conv-3"
    assert card.links.conversations == [4] and card.links.plan_id == 9 and card.links.branch == "card-7"
    assert card.questions[0].text == "Why?" and card.questions[0].answer is None
    assert (card.description, card.definition_of_done, card.plan) == ("Intro", "- [ ] one", "Do it")


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"title": "''"}, "title"),
        ({"title": "'" + "x" * 201 + "'"}, "title"),
        ({"title": None}, "title"),
        ({"kind": "epic"}, "kind"),
        ({"column": "doing"}, "column"),
        ({"version": "'-bad'"}, "version"),
        ({"version": "'" + "v" * 41 + "'"}, "version"),
        ({"origin": "{type: robot}"}, "origin.type"),
        ({"origin": "{type: user, ref: '" + "r" * 301 + "'}"}, "origin.ref"),
        ({"waits_for": "[1, 1]"}, "waits_for"),
        ({"waits_for": "[7]"}, "wait for itself"),
        ({"waits_for": "[" + ", ".join(str(n) for n in range(1, 22)) + "]"}, "waits_for"),
        ({"waits_for": "['1']"}, "waits_for"),
        ({"questions": "[{asked: 2026-11-02T08:00:00Z, text: '', answer: null}]"}, "questions"),
        ({"cost_usd": "-1"}, "cost_usd"),
        ({"cost_usd": "'3'"}, "cost_usd"),
        ({"cost_usd": "true"}, "cost_usd"),
        ({"links": "{plan_id: 0}"}, "links.plan_id"),
        ({"links": "{conversations: [" + ", ".join(str(n) for n in range(1, 52)) + "]}"}, "links.conversations"),
        ({"links": "{unknown: 1}"}, "links"),
        ({"created": "yesterday"}, "created"),
        ({"created": None}, "created"),
    ],
)
def test_a_bad_field_makes_a_broken_card_that_names_the_field(overrides: dict[str, object], message: str) -> None:
    result = parse_card(card_text(**overrides), PATH)
    assert isinstance(result, BrokenCard)
    assert result.card_id == 7
    assert message in result.error
    assert result.text is not None


def test_unknown_keys_are_kept_and_written_back_sorted() -> None:
    card = parsed(card_text(zeta="1", alpha="two"))
    assert card.extra == {"zeta": 1, "alpha": "two"}
    rendered = render_card(card)
    assert rendered.index("alpha: two") < rendered.index("zeta: 1")
    assert parsed(rendered).extra == card.extra
    assert render_card(parsed(rendered)) == rendered


def test_an_extra_key_cannot_repeat_a_card_field() -> None:
    with pytest.raises(InvalidCard):
        revise(parsed(card_text()), {"extra": {"title": "x"}})


def test_crlf_files_are_read_like_lf_files() -> None:
    text = card_text("Hello\n\n## Plan\n\nSteps\n")
    assert parsed(text.replace("\n", "\r\n")) == parsed(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "no front matter",
        "---\ntitle: [unclosed\n---\n",
        "---\n- a\n- list\n---\n",
        "---\n1: numeric key\n---\n",
        "---\nid: 7\n",
    ],
)
def test_broken_yaml_or_front_matter_never_raises(text: str) -> None:
    result = parse_card(text, PATH)
    assert isinstance(result, BrokenCard)
    assert result.error


def test_an_unsafe_yaml_tag_is_not_executed() -> None:
    text = "---\nid: 7\ntitle: !!python/object/apply:os.getcwd []\ncreated: x\nupdated: x\n---\n"
    assert isinstance(parse_card(text, PATH), BrokenCard)


@pytest.mark.parametrize("file_id", ["8", "'7'", "true", "7.0"])
def test_an_id_that_is_not_the_file_names_integer_is_broken(file_id: str) -> None:
    result = parse_card(card_text(id=file_id), PATH)
    assert isinstance(result, BrokenCard)
    assert result.card_id == 7 and result.error


@pytest.mark.parametrize("path", ["cards/x.md", "cards/07.md", "notes.md"])
def test_a_file_that_is_not_a_card_name_is_broken_without_an_id(path: str) -> None:
    result = parse_card(card_text(), path)
    assert isinstance(result, BrokenCard) and result.card_id is None


def test_a_file_over_the_size_cap_is_broken_and_its_text_is_not_kept() -> None:
    result = parse_card(card_text("x" * (MAX_FILE_BYTES + 1)), PATH)
    assert isinstance(result, BrokenCard)
    assert result.text is None and str(MAX_FILE_BYTES) in result.error


def test_writing_a_card_over_the_size_cap_is_refused() -> None:
    card = parsed(card_text())
    big = revise(card, {"description": "é" * 49_000, "plan": "é" * 49_000, "evidence": "é" * 49_000})
    with pytest.raises(InvalidCard):
        render_card(big)


def test_a_section_over_the_character_limit_is_broken() -> None:
    result = parse_card(card_text("## Plan\n\n" + "x" * 50_001), PATH)
    assert isinstance(result, BrokenCard) and "plan" in result.error


def test_a_plan_heading_inside_a_fence_stays_in_its_section() -> None:
    body = "Description\n\n```md\n## Plan\nnot a heading\n```\n\n## Plan\n\nreal plan\n"
    card = parsed(card_text(body))
    assert "## Plan" in card.description and "not a heading" in card.description
    assert card.plan == "real plan"


def test_a_tilde_fence_hides_headings_too() -> None:
    card = parsed(card_text("~~~\n## Evidence\n~~~\n"))
    assert card.evidence == "" and "## Evidence" in card.description


def test_text_before_the_first_heading_is_the_description() -> None:
    card = parsed(card_text("Just text\n\n## Evidence\n\nproof\n"))
    assert (card.description, card.evidence) == ("Just text", "proof")


def test_a_section_heading_inside_a_section_value_is_refused() -> None:
    with pytest.raises(InvalidCard):
        revise(parsed(card_text()), {"description": "a\n## Plan\nb"})


def test_a_section_that_ends_in_an_open_fence_is_closed_so_headings_survive() -> None:
    card = revise(parsed(card_text()), {"description": "```\ncode", "plan": "later"})
    again = parsed(render_card(card))
    assert again.plan == "later"
    assert again.description.endswith("```")


@pytest.mark.parametrize(
    "body",
    [
        "",
        "Only a description\n",
        "D\n\n## Definition of done\n\n- [x] a\n- [ ] b\n\n## Specification\n\nS\n\n## Plan\n\nP\n\n## Evidence\n\nE\n",
        "Unicode é ✓\n\n```py\nprint('## Plan')\n```\n",
    ],
)
def test_render_is_byte_stable_and_uses_lf(body: str) -> None:
    first = render_card(parsed(card_text(body, cost_usd="2.5", waits_for="[1]")))
    assert "\r" not in first
    assert render_card(parsed(first)) == first


def test_render_writes_the_front_matter_keys_in_the_fixed_order() -> None:
    text = render_card(parsed(card_text("Body")))
    block = text.split("---\n")[1]
    keys = [line.split(":")[0] for line in block.splitlines() if not line.startswith((" ", "-"))]
    assert keys == KEY_ORDER
    assert "created: 2026-11-01T10:00:00Z" in text


def test_sections_split_and_join_round_trip_and_leave_empty_ones_out() -> None:
    sections = split_sections("D\n\n## Plan\n\nP\n")
    assert sections["plan"] == "P" and sections["evidence"] == ""
    assert join_sections(sections) == "## Description\n\nD\n\n## Plan\n\nP\n"
    assert join_sections({}) == "## Description\n"


def test_validate_card_reports_a_safe_sentence() -> None:
    with pytest.raises(InvalidCard) as caught:
        validate_card({"id": 1, "title": "SECRET\nline", "created": WHEN, "updated": WHEN})
    assert "SECRET" not in str(caught.value)


def test_columns_are_in_flow_order() -> None:
    assert COLUMNS == ("inbox", "idea", "design", "plan", "build", "review", "done")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("- [ ] a\n- [x] b\n* [X] c\n+ [ ] d", [(False, "a"), (True, "b"), (True, "c"), (False, "d")]),
        ("  - [ ] indented", [(False, "indented")]),
        ("```\n- [ ] in fence\n```\n- [x] out", [(True, "out")]),
        ("- [] not an item\n-[ ] neither\n- [y] no", []),
        ("- [ ] crlf\r\n- [x] lines\r\n", [(False, "crlf"), (True, "lines")]),
        ("", []),
    ],
)
def test_checklist_items_are_found_outside_fences(text: str, expected: list[tuple[bool, str]]) -> None:
    assert parse_checklist(text) == [ChecklistItem(done, item) for done, item in expected]
