"""Positions in text as the Language Server Protocol counts them: 0-based lines split at \\r\\n, \\n or \\r, and
columns in UTF-16 code units (the protocol's default encoding, the only one YAAP offers)."""

import re
from dataclasses import dataclass
from typing import Any

LINE_BREAK = re.compile(r"\r\n|\r|\n")
# Identifier characters across the languages YAAP knows: letters, digits, _ and $ (JavaScript).
IDENTIFIER_CHAR = r"[\w$]"
# A model's line number is sometimes off by one or two; the symbol is looked for this far around it.
LINE_TOLERANCE = 2


class PositionError(ValueError):
    """A position the tool cannot resolve; the message says why, for the model."""


def split_lines(text: str) -> list[str]:
    """The lines without their breaks; a text ending in a break has an empty last line, as the protocol counts."""
    return LINE_BREAK.split(text)


def line_starts(text: str) -> list[int]:
    """The offset in `text` where each line starts."""
    return [0, *(match.end() for match in LINE_BREAK.finditer(text))]


def utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def index_from_utf16(line: str, column: int) -> int:
    """The index in `line` of a UTF-16 column; past the end gives the end."""
    units = 0
    for index, char in enumerate(line):
        if units >= column:
            return index
        units += 2 if ord(char) > 0xFFFF else 1
    return len(line)


def offset_of(text: str, starts: list[int], position: dict[str, Any]) -> int:
    """The offset in `text` of an LSP position {line, character}."""
    line_number = int(position["line"])
    if line_number >= len(starts):
        return len(text)
    start = starts[line_number]
    end = starts[line_number + 1] if line_number + 1 < len(starts) else len(text)
    line = LINE_BREAK.sub("", text[start:end])
    return start + index_from_utf16(line, int(position["character"]))


@dataclass(frozen=True)
class TextEdit:
    start: dict[str, Any]
    end: dict[str, Any]
    new_text: str

    @classmethod
    def from_lsp(cls, value: dict[str, Any]) -> "TextEdit":
        return cls(value["range"]["start"], value["range"]["end"], str(value.get("newText", "")))


def apply_edits(text: str, edits: list[TextEdit]) -> str:
    """The text with the edits applied, all on the original positions. Raises PositionError when two overlap."""
    starts = line_starts(text)
    spans = sorted(
        ((offset_of(text, starts, edit.start), offset_of(text, starts, edit.end), edit.new_text) for edit in edits),
        key=lambda span: (span[0], span[1]),
    )
    for (_, first_end, _), (second_start, _, _) in zip(spans, spans[1:], strict=False):
        if second_start < first_end:
            raise PositionError("The server's changes overlap, so they were not applied.")
    for start, end, new_text in reversed(spans):
        text = text[:start] + new_text + text[end:]
    return text


def symbol_pattern(symbol: str) -> re.Pattern[str]:
    escaped = re.escape(symbol)
    # Whole identifiers only, so `id` does not match inside `user_id`; other text is matched as it is.
    if re.fullmatch(rf"{IDENTIFIER_CHAR}+", symbol):
        return re.compile(rf"(?<!{IDENTIFIER_CHAR}){escaped}(?!{IDENTIFIER_CHAR})")
    return re.compile(escaped)


@dataclass(frozen=True)
class Resolved:
    """A position the tools ask the server about: 0-based line and UTF-16 column, and the 1-based line used."""

    line: int
    character: int
    used_line: int

    def lsp(self) -> dict[str, int]:
        return {"line": self.line, "character": self.character}


def resolve_position(text: str, line: int, symbol: str) -> Resolved:
    """The position of `symbol` on 1-based `line`: its first whole occurrence there, else on the nearest line within
    LINE_TOLERANCE. Raises PositionError with the line's text when it is not found."""
    symbol = symbol.strip()
    if not symbol:
        raise PositionError("Give the symbol's name as it is written on the line.")
    lines = split_lines(text)
    if line < 1 or line > len(lines):
        raise PositionError(f"Line {line} does not exist: the file has {len(lines)} lines.")
    pattern = symbol_pattern(symbol)
    for distance in range(LINE_TOLERANCE + 1):
        for candidate in sorted({line - distance, line + distance}):
            if 1 <= candidate <= len(lines):
                found = pattern.search(lines[candidate - 1])
                if found is not None:
                    character = utf16_length(lines[candidate - 1][: found.start()])
                    return Resolved(candidate - 1, character, candidate)
    shown = lines[line - 1].strip()[:200]
    raise PositionError(
        f"'{symbol}' is not on line {line} (nor within {LINE_TOLERANCE} lines of it). Line {line} is: {shown}"
    )


def line_text(lines: list[str], line: int) -> str:
    """A 0-based line's text, stripped and shortened for a result row."""
    return lines[line].strip()[:200] if 0 <= line < len(lines) else ""
