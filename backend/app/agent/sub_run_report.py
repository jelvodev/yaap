"""The report of a delegated sub-run and what the lead gets of it.

A worker ends with a few labelled lines (status, conclusion, changed files, left open). They are read leniently, as
models decorate labels, and a free-text report is kept as it is. The lead gets the report together with what YAAP
verified itself: its own list of changed files, the result of the check and the diff of those files, each capped.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from app.agent.escalation import CheckOutcome

ReportStatus = Literal["done", "blocked", "failed"]
MAX_REPORT = 2000
MAX_DIFF = 6000
MAX_CHECK_OUTPUT = 3000

_LABEL = re.compile(
    r"^\s*(?:[-*+]\s+)?\**\s*(status|conclusion|changed files|left open)\s*\**\s*:\s*\**\s*(.*)$", re.IGNORECASE
)
_DIFF_HEADER = re.compile(r"^diff --git a/(.+) b/(.+?)\r?$")
_NO_FILES = frozenset({"", "none", "-"})
_CHECK_WORDS: dict[CheckOutcome, str] = {"passed": "passed", "failed": "failed", "not_run": "could not run"}


@dataclass(frozen=True)
class SubRunReport:
    status: ReportStatus | None
    conclusion: str
    changed_files: tuple[str, ...]
    left_open: str
    structured: bool  # False when the text had no Status line (a free-text report)


@dataclass(frozen=True)
class CheckResult:
    command: str
    outcome: CheckOutcome
    output: str


def _status(value: str) -> ReportStatus | None:
    words = value.strip().lower().split()
    word = words[0].strip("`*.,;:") if words else ""
    if word == "done":
        return "done"
    if word == "blocked":
        return "blocked"
    if word == "failed":
        return "failed"
    return None


def _file_names(value: str) -> tuple[str, ...]:
    names: list[str] = []
    for line in value.splitlines():
        for part in re.sub(r"^\s*[-*+]\s+", "", line).split(","):
            name = part.strip().strip("`").strip()
            if name.lower() not in _NO_FILES:
                names.append(name)
    return tuple(names)


def parse_report(text: str) -> SubRunReport:
    """Read the labelled lines of a worker's report; a report without a Status label is returned as free text."""
    values: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in text.splitlines():
        match = _LABEL.match(line)
        if match:
            current = values.setdefault(match.group(1).lower(), [])
            current.append(match.group(2).rstrip("*").strip())
        elif current is not None:
            current.append(line)
    if "status" not in values:
        return SubRunReport(None, text.strip(), (), "", False)

    def joined(label: str) -> str:
        return "\n".join(values.get(label, [])).strip()

    return SubRunReport(
        status=_status(joined("status")),
        conclusion=joined("conclusion"),
        changed_files=_file_names(joined("changed files")),
        left_open=joined("left open"),
        structured=True,
    )


def capped(text: str, limit: int) -> str:
    """The text unchanged when within `limit` characters; else cut, with a note of how much was left out."""
    if len(text) <= limit:
        return text
    cut = text[:limit].rstrip()
    return f"{cut}\n\n[Cut at {limit:,} characters; {len(text) - len(cut):,} more were left out.]"


def files_diff(diff: str, paths: Iterable[str]) -> str:
    """The sections of a unified git diff that belong to `paths`, matched on the old or the new path."""
    wanted = set(paths)
    kept: list[str] = []
    keeping = False
    for line in diff.splitlines(keepends=True):
        header = _DIFF_HEADER.match(line)
        if header:
            keeping = bool({header.group(1), header.group(2)} & wanted)
        if keeping:
            kept.append(line)
    return "".join(kept)


def lead_report(report: SubRunReport, raw: str, check: CheckResult | None, diff: str, changed: Sequence[str]) -> str:
    """The text the lead model gets: the worker's report, then what YAAP verified itself."""
    if report.structured:
        lines = [f"Status: {report.status or 'unknown'}", f"Conclusion: {report.conclusion}"]
        if report.left_open:
            lines.append(f"Left open: {report.left_open}")
        body = "\n".join(lines)
    else:
        body = raw.strip()
    files = "\n".join(f"- {path}" for path in changed) or "- none"
    parts = [capped(body, MAX_REPORT), f"Changed files:\n{files}"]
    if check is None:
        parts.append("No check was given.")
    else:
        verdict = f"YAAP ran the check `{check.command}`: {_CHECK_WORDS[check.outcome]}."
        output = check.output.strip()
        parts.append(f"{verdict}\n{capped(output, MAX_CHECK_OUTPUT)}" if output else verdict)
    selected = files_diff(diff, changed)
    if selected:
        parts.append(f"Diff of the changed files:\n{capped(selected, MAX_DIFF)}")
    return "\n\n".join(parts)
