import pytest

from app.core.hidden_text import describe, find_hidden, mark_hidden, refusal_reason, refusing

ZWSP = "​"
ZWJ = "‍"
BOM = "﻿"
RLO = "‮"
TAG_A = "\U000e0061"
CYRILLIC_A = "а"
GREEK_ALPHA = "α"


@pytest.mark.parametrize(
    ("text", "kind", "line", "column", "code_point"),
    [
        (f"hello {TAG_A}", "tag", 1, 7, 0xE0061),
        (f"a{RLO}b", "bidi", 1, 2, 0x202E),
        ("a\n⁦b", "bidi", 2, 1, 0x2066),
        ("x‏y", "bidi", 1, 2, 0x200F),
        (f"zero{ZWSP}width", "invisible", 1, 5, 0x200B),
        ("line\nword⁠joiner", "invisible", 2, 5, 0x2060),
        (f"a{BOM}b", "invisible", 1, 2, 0xFEFF),
        (f"{ZWJ}start", "invisible", 1, 1, 0x200D),
        (f"a{ZWJ}b", "invisible", 1, 2, 0x200D),
        ("soft­hyphen", "invisible", 1, 5, 0x00AD),
        ("aㅤb", "invisible", 1, 2, 0x3164),
        ("plain️", "variation", 1, 6, 0xFE0F),
        ("︀ first", "variation", 1, 1, 0xFE00),
        (f"p{CYRILLIC_A}ypal", "lookalike", 1, 2, 0x0430),
        (f"{GREEK_ALPHA}pple", "lookalike", 1, 1, 0x03B1),
    ],
)
def test_each_kind_is_found_with_its_position(text: str, kind: str, line: int, column: int, code_point: int) -> None:
    findings = find_hidden(text)
    assert [(f.kind, f.line, f.column, f.code_point) for f in findings] == [(kind, line, column, code_point)]
    assert findings[0].name


@pytest.mark.parametrize(
    "text",
    [
        "",
        "plain ASCII text",
        "Café crème naïve über Ångström",
        "Emoji \U0001f600 and ❤️ and \U0001f44d\U0001f3fd",
        f"family \U0001f468{ZWJ}\U0001f469{ZWJ}\U0001f467",
        f"fire ❤️{ZWJ}\U0001f525",
        "keycap 1️⃣",
        "CJK 你好 こんにちは 漢︀字",
        f"{BOM}file that starts with a byte order mark",
        "Привет мир and αβγ in separate words",
        "England \U0001f3f4\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f and the rest",
        "tabs\tand\r\nwindows lines and — dashes “quotes”",
    ],
)
def test_allowed_and_plain_text_is_clean(text: str) -> None:
    assert find_hidden(text) == []


def test_lookalike_reports_the_word_and_every_foreign_letter() -> None:
    word = f"p{CYRILLIC_A}yp{CYRILLIC_A}l"
    findings = find_hidden(f"visit {word} now")
    assert [(f.kind, f.word, f.column) for f in findings] == [("lookalike", word, 8), ("lookalike", word, 11)]


@pytest.mark.parametrize(
    ("text", "refused"),
    [
        (f"a{TAG_A}", True),
        (f"a{RLO}", True),
        (f"a{ZWSP}", False),
        ("a️", False),
        (f"p{CYRILLIC_A}ypal", False),
        ("clean", False),
        (f"a{ZWSP}b{TAG_A}", True),
    ],
)
def test_only_tag_and_bidi_findings_are_refused(text: str, refused: bool) -> None:
    findings = find_hidden(text)
    assert refusing(findings) is refused
    assert (refusal_reason(findings) is not None) is refused


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"a{ZWSP}b", "a⟦U+200B ZERO WIDTH SPACE⟧b"),
        (f"p{CYRILLIC_A}y", "p⟦U+0430 CYRILLIC SMALL LETTER A⟧y"),
        ("nothing to see", "nothing to see"),
    ],
)
def test_mark_hidden_makes_the_character_visible(text: str, expected: str) -> None:
    assert mark_hidden(text) == expected


def test_describe_names_each_kind_and_is_empty_for_clean_text() -> None:
    assert describe(find_hidden("clean")) == ""
    text = describe(find_hidden(f"a{ZWSP}b{TAG_A}c p{CYRILLIC_A}ypal"))
    assert "tag character" in text
    assert "invisible character" in text
    assert "line 1, column 2" in text
    assert f"p{CYRILLIC_A}ypal" in text


@pytest.mark.parametrize(
    "text",
    [
        "\U000e0067\U000e0062\U000e007f without the flag",
        "\U0001f3f4\U000e0067\U000e0062\U000e0065 without the cancel tag",
        "\U0001f3f4\U000e0049\U000e0067\U000e006e\U000e006f\U000e0072\U000e0065\U000e007f capital letters",
        "\U0001f3f4\U000e0061\U000e0062\U000e0063\U000e0064\U000e0065\U000e0066\U000e0067\U000e0068\U000e007f too long",
    ],
)
def test_tag_characters_outside_a_flag_are_refused(text: str) -> None:
    assert refusing(find_hidden(text))
