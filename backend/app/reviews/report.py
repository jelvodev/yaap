"""The review's prompt and report: pure functions over text.

A diff is split per file. A small diff goes to the reviewer in one request; a large one is reviewed in parts of whole
files, up to MAX_PARTS requests, and a file larger than a part is shortened. What is shortened or left out becomes a
note on the report. The reviewer answers with a JSON object of findings (file, line, severity, explanation), which
parse_report() reads leniently: an unknown severity becomes medium, a line that is not a positive number none.
"""

import json
import re
from dataclasses import dataclass
from typing import Literal, get_args

from app.blueprint.builtin import render, shipped_instructions

Severity = Literal["high", "medium", "low"]
SEVERITIES: tuple[Severity, ...] = get_args(Severity)

# What one review request holds of the diff, and how many requests a review makes at most.
MAX_PART_CHARS = 40_000
MAX_PARTS = 4
MAX_RULES_CHARS = 8_000
MAX_OBJECTIVE_CHARS = 4_000
MAX_FINDINGS = 100
MAX_EXPLANATION_CHARS = 2_000
MAX_SUMMARY_CHARS = 2_000
# How many left-out files a note names before it counts the rest.
NAMED_FILES = 20

FILE_HEADER = re.compile(r"^(?=diff --git )", re.MULTILINE)
REPORT_OBJECT = re.compile(r"\{.*\}", re.DOTALL)
# The three severities, and what reviewers write instead of them.
SEVERITY_WORDS: dict[str, Severity] = {
    "high": "high",
    "medium": "medium",
    "low": "low",
    "critical": "high",
    "blocker": "high",
    "major": "high",
    "error": "high",
    "moderate": "medium",
    "warning": "medium",
    "minor": "low",
    "info": "low",
    "nit": "low",
    "suggestion": "low",
}


@dataclass(frozen=True)
class FileDiff:
    path: str
    text: str


@dataclass(frozen=True)
class ReviewParts:
    """The diff as the reviewer gets it: per request the files it holds, and what was shortened or left out."""

    parts: tuple[tuple[FileDiff, ...], ...]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class Finding:
    path: str
    line: int | None
    severity: Severity
    explanation: str

    def stored(self) -> dict[str, str | int | None]:
        return {"path": self.path, "line": self.line, "severity": self.severity, "explanation": self.explanation}


class ReportError(Exception):
    """The reviewer's answer holds no report; the message says so."""


def diff_path(section: str) -> str:
    """The file a section of a diff is about: its new name, else its old name (a deleted file), else the header."""
    for pattern in (r"^\+\+\+ b/(.+)$", r"^--- a/(.+)$", r"^diff --git a/.+ b/(.+)$"):
        found = re.search(pattern, section, re.MULTILINE)
        if found:
            return found.group(1).strip()
    return "(other)"


def split_files(diff: str) -> list[FileDiff]:
    """One part per file; text before the first file header (a note that a new file was too large) is its own part."""
    return [FileDiff(diff_path(section), section) for section in FILE_HEADER.split(diff) if section.strip()]


def named(paths: list[str]) -> str:
    shown = ", ".join(paths[:NAMED_FILES])
    return shown + (f" and {len(paths) - NAMED_FILES} more" if len(paths) > NAMED_FILES else "")


def plan_parts(files: list[FileDiff], max_chars: int = MAX_PART_CHARS, max_parts: int = MAX_PARTS) -> ReviewParts:
    """Whole files packed into at most max_parts requests of at most max_chars each, in the diff's order."""
    notes: list[str] = []
    shortened: list[str] = []
    parts: list[list[FileDiff]] = []
    left_out: list[str] = []
    size = 0
    for file in files:
        if len(file.text) > max_chars:
            shortened.append(file.path)
            file = FileDiff(file.path, file.text[:max_chars] + "\n[The rest of this file's diff is not shown.]\n")
        if parts and size + len(file.text) <= max_chars and not left_out:
            parts[-1].append(file)
            size += len(file.text)
        elif len(parts) < max_parts and not left_out:
            parts.append([file])
            size = len(file.text)
        else:
            left_out.append(file.path)
    if shortened:
        notes.append(f"Shortened to their first {max_chars:,} characters of diff: {named(shortened)}.")
    if left_out:
        notes.append(f"Not reviewed, as the diff is larger than a review takes: {named(left_out)}.")
    if len(parts) > 1:
        notes.append(f"The diff was reviewed per file, in {len(parts)} parts.")
    return ReviewParts(tuple(tuple(part) for part in parts), tuple(notes))


def review_prompt(
    objective: str | None,
    rules: str | None,
    files: tuple[FileDiff, ...],
    part: int,
    parts: int,
    instructions: str | None = None,
) -> str:
    """The reviewer's instructions, which the user may replace; the objective, the rules and the diff are fenced off
    as material, never as instructions. The answer format that parse_report() reads stays here."""
    goal = (objective or "").strip()[:MAX_OBJECTIVE_CHARS] or "(not given: review the changes on their own merits)"
    standards = (rules or "").strip()[:MAX_RULES_CHARS] or "(the project has no standards)"
    diff = "".join(file.text for file in files)
    which = f" This is part {part} of {parts} of the diff; review only the files in it." if parts > 1 else ""
    lead = render(instructions if instructions is not None else shipped_instructions("reviewer"), {"which": which})
    return (
        f"{lead}\n\n"
        f"<material>\nWhat the changes were meant to do:\n{goal}\n\nThe project's standards:\n{standards}\n\n"
        f"The diff:\n{diff}\n</material>\n\n"
        'Reply with only a JSON object: {"summary": "two or three sentences on the changes overall", "findings": '
        '[{"path": "file path as in the diff", "line": line number in the new version of the file or null, '
        '"severity": "high" | "medium" | "low", "explanation": "what is wrong and what to do instead"}]}. '
        "high: a bug, data loss or a security problem; medium: likely wrong or fragile; low: worth a look. "
        'An empty "findings" list is a fine answer when you find nothing.'
    )


def severity_of(value: object) -> Severity:
    word = value.strip().lower() if isinstance(value, str) else ""
    return SEVERITY_WORDS.get(word, "medium")


def line_of(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip()) or None
    return None


def parse_report(text: str) -> tuple[str, list[Finding]]:
    """The reviewer's summary and findings, from the JSON object in its answer."""
    found = REPORT_OBJECT.search(text)
    if found is None:
        raise ReportError("The reviewer's answer holds no report.")
    try:
        report = json.loads(found.group(0))
    except json.JSONDecodeError as error:
        raise ReportError("The reviewer's answer is not a valid report.") from error
    if not isinstance(report, dict) or not isinstance(report.get("findings", []), list):
        raise ReportError("The reviewer's report has no list of findings.")
    summary = report.get("summary")
    findings = []
    for item in report.get("findings", [])[:MAX_FINDINGS]:
        if not isinstance(item, dict):
            continue
        path, explanation = item.get("path"), item.get("explanation")
        if not isinstance(explanation, str) or not explanation.strip():
            continue
        where = path.strip() if isinstance(path, str) and path.strip() else "(general)"
        line, severity = line_of(item.get("line")), severity_of(item.get("severity"))
        findings.append(Finding(where, line, severity, explanation.strip()[:MAX_EXPLANATION_CHARS]))
    return (summary.strip()[:MAX_SUMMARY_CHARS] if isinstance(summary, str) else ""), findings


def ordered(findings: list[Finding]) -> list[Finding]:
    """High first, then by file and line."""
    return sorted(findings, key=lambda item: (SEVERITIES.index(item.severity), item.path, item.line or 0))
