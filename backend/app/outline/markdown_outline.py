"""The outline of a Markdown file: its headings, nested by level, outside fenced code blocks."""

import re
from dataclasses import dataclass, field

from app.outline.symbols import Symbol

FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)(?:\s+#+)?\s*$")


@dataclass
class _Heading:
    level: int
    title: str
    line: int
    end_line: int = 0
    children: list["_Heading"] = field(default_factory=list)


def markdown_symbols(text: str) -> tuple[Symbol, ...]:
    lines = text.splitlines()
    roots: list[_Heading] = []
    open_headings: list[_Heading] = []
    fence = ""
    for number, line in enumerate(lines, start=1):
        marker = FENCE.match(line)
        if marker is not None and (not fence or marker.group(1).startswith(fence)):
            fence = "" if fence else marker.group(1)[:3]
            continue
        heading = HEADING.match(line) if not fence else None
        if heading is None:
            continue
        level = len(heading.group(1))
        while open_headings and open_headings[-1].level >= level:
            open_headings.pop().end_line = number - 1
        found = _Heading(level, heading.group(2), number)
        (open_headings[-1].children if open_headings else roots).append(found)
        open_headings.append(found)
    for heading in open_headings:
        heading.end_line = len(lines)
    return tuple(_frozen(heading) for heading in roots)


def _frozen(heading: _Heading) -> Symbol:
    return Symbol(
        "heading",
        heading.title,
        heading.line,
        heading.end_line,
        "#" * heading.level,
        tuple(_frozen(child) for child in heading.children),
    )
