"""The acceptance criteria of a specification, read from its Markdown.

A criterion is a list item under a `### Acceptance criteria` heading: `- [ ] text`, optionally ending in one code
span that says how YAAP checks it:

- `` `run: <command>` ``: a command in the project that must exit with 0 (a CommandCheck);
- `` `file: <path> exists` `` or `` `file: <path> contains <text>` ``: a file in the project (a FileCheck);
- nothing: the reviewer judges it, with the criterion as the rubric (a RubricCheck).

Parsing is a pure function of the text. Lists inside a fenced code block are examples, not criteria, and are ignored.
Every problem is collected, so one look at a broken specification shows all that is wrong.
"""

import re
from dataclasses import dataclass

from app.evals.cases import DEFAULT_COMMAND_TIMEOUT, workspace_path
from app.evals.models import CommandCheck, FileCheck, RubricCheck

MAX_CRITERIA = 30
MAX_NAME_CHARS = 120
CRITERIA_HEADING = re.compile(r"^ {0,3}###[ \t]+acceptance criteria[ \t]*#*[ \t]*$", re.IGNORECASE)
# A heading of level 1 to 3 ends the criteria section; a deeper one is part of it.
SECTION_END = re.compile(r"^ {0,3}#{1,3}(?:[ \t]|$)")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
ITEM = re.compile(r"^[ \t]*[-*+][ \t]+\[([ xX])\][ \t]?(.*)$")
OTHER_ITEM = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]")
# One code span at the very end of the item, as in `run: pytest`; a span may cross a line break in Markdown.
TRAILING_SPAN = re.compile(r"(`+)(.+?)\1\s*$", re.DOTALL)
FILE_CHECK = re.compile(r"^file:\s*(\S+)\s+(?:(exists)|contains\s+(.+))$", re.DOTALL)


@dataclass(frozen=True)
class Criterion:
    number: int
    text: str
    check: CommandCheck | FileCheck | RubricCheck


def parse_criteria(markdown: str) -> list[Criterion] | list[str]:
    """The criteria of a specification in order, or the problems that make them unusable. No section, or an empty
    one, gives an empty list."""
    items = criteria_items(markdown)
    problems: list[str] = []
    if len(items) > MAX_CRITERIA:
        problems.append(f"A specification can have at most {MAX_CRITERIA} acceptance criteria; it has {len(items)}.")
    criteria: list[Criterion] = []
    for number, item in enumerate(items, start=1):
        criterion = parse_item(number, item, problems)
        if criterion is not None:
            criteria.append(criterion)
    return problems if problems else criteria


def criteria_items(markdown: str) -> list[str]:
    """The raw text of each checkbox item in the first criteria section, with continuation lines joined by a line
    break. Lines inside a fenced code block never count."""
    items: list[list[str]] = []
    fence: str | None = None
    in_section = False
    current: list[str] | None = None
    for line in markdown.splitlines():
        opened = FENCE.match(line)
        if fence is not None:
            if opened is not None and opened.group(1)[0] == fence[0] and len(opened.group(1)) >= len(fence):
                fence = None
            continue
        if opened is not None:
            fence = opened.group(1)
            current = None
            continue
        if not in_section:
            in_section = CRITERIA_HEADING.match(line) is not None
            continue
        if SECTION_END.match(line):
            break
        item = ITEM.match(line)
        if item is not None:
            current = [item.group(2).strip()]
            items.append(current)
        elif not line.strip() or OTHER_ITEM.match(line):
            current = None
        elif current is not None:
            current.append(line.strip())
    return ["\n".join(lines) for lines in items]


def parse_item(number: int, item: str, problems: list[str]) -> Criterion | None:
    where = f"Criterion {number}"
    span = TRAILING_SPAN.search(item)
    code = span.group(2).strip() if span is not None else ""
    if span is None or not code.startswith(("run:", "file:")):
        text = one_line(item)
        if not text:
            problems.append(f"{where} is empty.")
            return None
        return Criterion(number, text, RubricCheck(check_name(number, text), text))
    text = one_line(item[: span.start()])
    if "\n" in code or "\r" in code:
        problems.append(f"{where}: a check must be on one line.")
        return None
    if code.startswith("run:"):
        command = code.removeprefix("run:").strip()
        if not command:
            problems.append(f"{where}: `run:` needs the command to run.")
            return None
        text = text or command
        return Criterion(number, text, CommandCheck(check_name(number, text), command, DEFAULT_COMMAND_TIMEOUT))
    return file_criterion(number, text, code, problems)


def file_criterion(number: int, text: str, code: str, problems: list[str]) -> Criterion | None:
    where = f"Criterion {number}"
    found = FILE_CHECK.match(code)
    if found is None:
        problems.append(f"{where}: write the file check as `file: <path> exists` or `file: <path> contains <text>`.")
        return None
    path = workspace_path(found.group(1), f"{where}: the path", problems)
    if path is None:
        return None
    text = text or code
    name = check_name(number, text)
    if found.group(2) is not None:
        return Criterion(number, text, FileCheck(name, path))
    return Criterion(number, text, FileCheck(name, path, contains=(found.group(3).strip(),)))


def one_line(text: str) -> str:
    return " ".join(text.split())


def check_name(number: int, text: str) -> str:
    return f"{number}. {text}"[:MAX_NAME_CHARS]
