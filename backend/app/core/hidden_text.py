"""Characters the reader of a text cannot see, found in text that comes from outside.

A skill from GitHub, a Blueprint package, an imported document or session can hide instructions in tag characters,
reorder text with direction controls, or hide behind zero-width characters and letters that look like Latin ones.
`find_hidden` is a pure function; the reviews of outside text show its findings and `mark_hidden` makes them visible.
Tag characters and direction controls are refused (they have no place in Markdown instructions); the other kinds are
warnings the user sees before accepting the text.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

HiddenKind = Literal["tag", "bidi", "invisible", "variation", "lookalike"]

REFUSED_KINDS: frozenset[str] = frozenset({"tag", "bidi"})
BIDI_CONTROLS = frozenset(
    [*range(0x202A, 0x202F), *range(0x2066, 0x206A), 0x200E, 0x200F, 0x061C]
)
# Not format characters (category Cf), but blank when drawn.
BLANK_LETTERS = frozenset({0x115F, 0x1160, 0x3164, 0xFFA0})
ZERO_WIDTH_JOINER = 0x200D
BYTE_ORDER_MARK = 0xFEFF
KEYCAP = 0x20E3
KEYCAP_BASES = frozenset("0123456789#*")
LETTERS = re.compile(r"[^\W\d_]+")
MIXED_SCRIPTS = frozenset({"CYRILLIC", "GREEK"})
EXAMPLES = 3


@dataclass(frozen=True)
class HiddenFinding:
    kind: HiddenKind
    # 1-based, counted in characters.
    line: int
    column: int
    code_point: int
    name: str
    # The whole word, for a lookalike.
    word: str = ""

    @property
    def label(self) -> str:
        return f"U+{self.code_point:04X} {self.name}"


def is_variation_selector(code: int) -> bool:
    return 0xFE00 <= code <= 0xFE0F or 0xE0100 <= code <= 0xE01EF


def is_emoji(code: int) -> bool:
    """Emoji and the symbol blocks that hold them; a close approximation, since the standard library has no table."""
    return (
        0x1F000 <= code <= 0x1FAFF
        or 0x2190 <= code <= 0x2BFF
        or 0x1F3FB <= code <= 0x1F3FF
        or code in (0x00A9, 0x00AE, 0x203C, 0x2049, 0x2122, 0x2139, 0x3030, 0x303D, 0x3297, 0x3299)
    )


def is_cjk(code: int) -> bool:
    return (
        0x2E80 <= code <= 0x2FDF
        or 0x3000 <= code <= 0x30FF
        or 0x31F0 <= code <= 0x31FF
        or 0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0xFF00 <= code <= 0xFFEF
        or 0x20000 <= code <= 0x323AF
    )


def char_name(char: str) -> str:
    return unicodedata.name(char, f"CHARACTER {ord(char):04X}")


def script_of(char: str) -> str:
    return char_name(char).split(" ", 1)[0]


def allowed_variation(text: str, index: int) -> bool:
    """A variation selector that follows an emoji (or a CJK character, or a keycap base before U+20E3)."""
    if index == 0:
        return False
    previous = text[index - 1]
    code = ord(previous)
    if is_variation_selector(code):  # A sequence such as a selector after a selector: judge the character before it.
        return index >= 2 and allowed_variation(text, index - 1)
    if previous in KEYCAP_BASES:
        return index + 1 < len(text) and ord(text[index + 1]) == KEYCAP
    return is_emoji(code) or is_cjk(code)


def allowed_joiner(text: str, index: int) -> bool:
    """A zero-width joiner between two emoji (the first may carry a variation selector)."""
    if index == 0 or index + 1 >= len(text):
        return False
    before = ord(text[index - 1])
    if before == 0xFE0F and index >= 2:
        before = ord(text[index - 2])
    return is_emoji(before) and is_emoji(ord(text[index + 1]))


def classify(text: str, index: int) -> HiddenKind | None:
    char = text[index]
    code = ord(char)
    if 0xE0000 <= code <= 0xE007F:
        return "tag"
    if code in BIDI_CONTROLS:
        return "bidi"
    if is_variation_selector(code):
        return None if allowed_variation(text, index) else "variation"
    if code == BYTE_ORDER_MARK and index == 0:
        return None
    if code == ZERO_WIDTH_JOINER and allowed_joiner(text, index):
        return None
    if unicodedata.category(char) == "Cf" or code in BLANK_LETTERS:
        return "invisible"
    return None


def scan(text: str) -> list[tuple[int, HiddenFinding]]:
    """Every finding with the index of its character in the text, in text order."""
    found: list[tuple[int, HiddenFinding]] = []
    line_start = 0
    line = 1
    lookalike_at: dict[int, str] = {}
    for word in LETTERS.finditer(text):
        scripts = {script_of(char) for char in word.group()}
        if "LATIN" in scripts and scripts & MIXED_SCRIPTS:
            for offset, char in enumerate(word.group()):
                if script_of(char) != "LATIN":
                    lookalike_at[word.start() + offset] = word.group()
    for index, char in enumerate(text):
        if char == "\n":
            line += 1
            line_start = index + 1
            continue
        kind = classify(text, index) if ord(char) > 0x7F else None
        word = ""
        if kind is None and index in lookalike_at:
            kind, word = "lookalike", lookalike_at[index]
        if kind is not None:
            found.append((index, HiddenFinding(kind, line, index - line_start + 1, ord(char), char_name(char), word)))
    return found


def find_hidden(text: str) -> list[HiddenFinding]:
    """The characters in `text` that a reader cannot see or that pass for other letters, in text order."""
    return [finding for _, finding in scan(text)]


def refusing(findings: list[HiddenFinding]) -> bool:
    """Tag characters and direction controls make the text unacceptable; the other kinds only warn."""
    return any(finding.kind in REFUSED_KINDS for finding in findings)


def first_position(findings: list[HiddenFinding]) -> str:
    first = findings[0]
    return f"{first.label} at line {first.line}, column {first.column}"


def warning_lines(findings: list[HiddenFinding]) -> list[str]:
    """One sentence per kind of finding, for a list of warnings."""
    lines: list[str] = []
    for kind, text in (
        ("tag", "Contains {count} Unicode tag character(s), which can hide instructions (first: {first})."),
        ("bidi", "Contains {count} text direction control(s), which can make the text read differently than it is "
                 "(first: {first})."),
        ("invisible", "Contains {count} invisible character(s) (first: {first})."),
        ("variation", "Contains {count} variation selector(s) that do not follow an emoji (first: {first})."),
    ):
        of_kind = [finding for finding in findings if finding.kind == kind]
        if of_kind:
            lines.append(text.format(count=len(of_kind), first=first_position(of_kind)))
    words = list(dict.fromkeys(finding.word for finding in findings if finding.kind == "lookalike"))
    if words:
        more = f" and {len(words) - EXAMPLES} more" if len(words) > EXAMPLES else ""
        shown = ", ".join(f"\"{word}\"" for word in words[:EXAMPLES]) + more
        lines.append(f"Contains words that mix Latin letters with Cyrillic or Greek ones: {shown}.")
    return lines


def describe(findings: list[HiddenFinding]) -> str:
    """A short warning text for the findings; empty when there are none."""
    return " ".join(warning_lines(findings))


def refusal_reason(findings: list[HiddenFinding]) -> str | None:
    """Why YAAP refuses the text, or None when it may be accepted."""
    if not refusing(findings):
        return None
    refused = [finding for finding in findings if finding.kind in REFUSED_KINDS]
    return f"{' '.join(warning_lines(refused))} YAAP refuses text with these characters."


def mark_hidden(text: str) -> str:
    """The text with each finding's character replaced by a visible mark such as `⟦U+200B ZERO WIDTH SPACE⟧`."""
    hits = {index: finding for index, finding in scan(text)}
    if not hits:
        return text
    return "".join(f"⟦{hits[index].label}⟧" if index in hits else char for index, char in enumerate(text))
