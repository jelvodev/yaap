"""The outline's shape and the interface that produces it.

`Outliner` is what `read_file` asks for an outline. The simple parsers per language implement it today; a language
server's document symbols can implement the same interface and fall back to the parsers where no server runs.
"""

from dataclasses import dataclass
from typing import Literal, Protocol

SymbolKind = Literal["class", "function", "method", "interface", "type", "enum", "heading"]
MAX_DETAIL_CHARS = 80


@dataclass(frozen=True)
class Symbol:
    kind: SymbolKind
    name: str
    # 1-based and inclusive, so `offset=line, limit=end_line - line + 1` reads exactly this symbol.
    line: int
    end_line: int
    # A short addition after the name: a signature, base classes, or a heading's level marks.
    detail: str = ""
    children: tuple["Symbol", ...] = ()


@dataclass(frozen=True)
class Outline:
    symbols: tuple[Symbol, ...]
    # Why the file has no outline although its type is known, such as a syntax error.
    problem: str = ""


class OutlineError(Exception):
    """The parser could not read the file; the message says why, for the model."""


class Outliner(Protocol):
    async def outline(self, path: str, text: str) -> Outline | None:
        """The file's outline, or None when this kind of file has no outline."""
        ...


def short_detail(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= MAX_DETAIL_CHARS else text[: MAX_DETAIL_CHARS - 1] + "…"


def render_outline(path: str, line_count: int, outline: Outline | None) -> str:
    if outline is None:
        return (
            f"No outline is available for this type of file ({path}, {line_count:,} lines). Read it with offset and "
            "limit instead."
        )
    if outline.problem:
        return f"No outline of {path}: {outline.problem} Read it with offset and limit instead."
    if not outline.symbols:
        return f"{path} ({line_count:,} lines) has no classes, functions or headings to outline."
    rows = list(_rows(outline.symbols, 0))
    return (
        f"Outline of {path} ({line_count:,} lines): line range, then the symbol. Read a part with offset and limit.\n"
        + "\n".join(rows)
    )


def _rows(symbols: tuple[Symbol, ...], depth: int) -> list[str]:
    rows: list[str] = []
    for symbol in symbols:
        span = f"{symbol.line}-{symbol.end_line}" if symbol.end_line > symbol.line else str(symbol.line)
        label = f"{symbol.detail} {symbol.name}" if symbol.kind == "heading" else f"{symbol.kind} {symbol.name}"
        detail = "" if symbol.kind == "heading" else symbol.detail
        rows.append(f"{span:>11}  {'  ' * depth}{label}{detail}")
        rows.extend(_rows(symbol.children, depth + 1))
    return rows
