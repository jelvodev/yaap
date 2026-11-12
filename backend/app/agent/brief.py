"""Delegation by brief: the structured task a lead agent hands to a worker.

A brief is the task plus what the worker would otherwise have to find out: the files to start from, what is already
known, when the work counts as done and a command that proves it. YAAP runs that command itself after the worker
finishes, so the worker's claim that it passes is never the only evidence. This module only parses and words briefs;
it does no I/O, so the same text is produced for the first attempt, the retry and the takeover.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass

MAX_FILES = 20
MAX_FACTS = 4000
MAX_DONE_WHEN = 2000
MAX_CHECK = 500

_LINE_RANGE = re.compile(r"^(?P<path>.*):(?P<start>\d+)(?:-(?P<end>\d+))?$")
_DRIVE = re.compile(r"^[A-Za-z]:")


class BriefError(Exception):
    """A brief that cannot be used; the message goes back to the model as a tool error, so it says what to fix."""


@dataclass(frozen=True)
class FileRef:
    path: str
    start: int | None = None  # first line, 1-based
    end: int | None = None  # last line, inclusive

    def label(self) -> str:
        """The reference as the model wrote it: `src/a.py`, `src/a.py:12` or `src/a.py:12-40`."""
        if self.start is None:
            return self.path
        if self.end is None or self.end == self.start:
            return f"{self.path}:{self.start}"
        return f"{self.path}:{self.start}-{self.end}"


@dataclass(frozen=True)
class Brief:
    task: str
    files: tuple[FileRef, ...] = ()
    facts: str = ""
    done_when: str = ""
    check: str | None = None

    def has_brief(self) -> bool:
        """Whether anything beyond the task is given."""
        return bool(self.files or self.facts or self.done_when or self.check)


def _has_control(text: str) -> bool:
    return "\n" in text or "\r" in text or "\0" in text


def parse_file_ref(value: str) -> FileRef:
    """Parse `path`, `path:12` or `path:12-40`; the path must be relative."""
    text = value.strip()
    if not text:
        raise BriefError("A file reference in `files` is empty; give a relative path such as src/a.py:12-40.")
    if _has_control(text):
        raise BriefError("A file reference in `files` must be a single line without control characters.")
    start: int | None = None
    end: int | None = None
    path = text
    # Split on the last colon only when digits follow, so a colon inside the path stays part of it.
    match = _LINE_RANGE.match(text)
    if match:
        path = match.group("path")
        start = int(match.group("start"))
        end = int(match.group("end")) if match.group("end") else None
    if not path:
        raise BriefError(f"File reference '{value}' has no path; give a relative path such as src/a.py.")
    if path.startswith(("/", "\\")) or _DRIVE.match(path):
        raise BriefError(f"File reference '{value}' is an absolute path; give a path relative to the workspace.")
    if start is not None and start < 1:
        raise BriefError(f"File reference '{value}' starts before line 1; line numbers start at 1.")
    if start is not None and end is not None and end < start:
        raise BriefError(f"File reference '{value}' ends before it starts; use path:first-last with last >= first.")
    return FileRef(path, start, end)


def _text_field(arguments: Mapping[str, object], name: str, limit: int) -> str:
    value = arguments.get(name)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise BriefError(f"`{name}` must be a string.")
    text = value.strip()
    if len(text) > limit:
        raise BriefError(f"`{name}` is {len(text):,} characters; keep it within {limit:,}.")
    return text


def _files_field(arguments: Mapping[str, object]) -> tuple[FileRef, ...]:
    value = arguments.get("files")
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise BriefError('`files` must be a list of strings such as ["src/a.py:12-40"].')
    if len(value) > MAX_FILES:
        raise BriefError(f"`files` lists {len(value)} files; give at most {MAX_FILES}.")
    return tuple(parse_file_ref(item) for item in value)


def parse_brief(arguments: Mapping[str, object]) -> Brief:
    """Read the brief out of the `delegate` arguments; keys that are not part of a brief are ignored."""
    task = arguments.get("task")
    if not isinstance(task, str) or not task.strip():
        raise BriefError("`task` is required: a non-empty description of what the worker must do.")
    check = _text_field(arguments, "check", MAX_CHECK)
    if _has_control(check):
        raise BriefError("`check` must be a single-line command without control characters.")
    return Brief(
        task=task.strip(),
        files=_files_field(arguments),
        facts=_text_field(arguments, "facts", MAX_FACTS),
        done_when=_text_field(arguments, "done_when", MAX_DONE_WHEN),
        check=check or None,
    )


def brief_message(brief: Brief) -> str:
    """The message the worker starts from; without a brief it is the task exactly as given."""
    if not brief.has_brief():
        return brief.task
    sections = [brief.task]
    if brief.files:
        sections.append("Files to start from:\n" + "\n".join(f"- {ref.label()}" for ref in brief.files))
    if brief.facts:
        sections.append(f"What is already known:\n{brief.facts}")
    if brief.done_when:
        sections.append(f"Done when:\n{brief.done_when}")
    if brief.check:
        heading = "Check (YAAP runs it after you finish; your own claim that it passes is not enough):"
        sections.append(f"{heading}\n`{brief.check}`")
    return "\n\n".join(sections)


def retry_message(brief: Brief, check_output: str, report: str) -> str:
    """The brief again after a failed check, with the failure and the worker's previous report."""
    return (
        f"{brief_message(brief)}\n\n"
        "Your previous attempt did not pass the check.\n\n"
        f"Check output:\n{check_output.strip() or '(no output)'}\n\n"
        f"Your previous report:\n{report.strip() or '(empty)'}\n\n"
        "Fix what the check shows, then report again."
    )


def takeover_message(brief: Brief, worker_report: str, diff: str, check_output: str) -> str:
    """The brief for the main model after a worker failed the check twice."""
    return (
        f"{brief_message(brief)}\n\n"
        "A worker tried this twice and the check still failed. Its changes are in the files now; continue from "
        "their current state.\n\n"
        f"The worker's last report:\n{worker_report.strip() or '(empty)'}\n\n"
        f"Diff of its changes:\n{diff.strip() or 'no diff'}\n\n"
        f"Check output:\n{check_output.strip() or '(no output)'}"
    )
