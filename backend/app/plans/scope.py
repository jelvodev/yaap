"""Whether a step kept to its plan: the files its commit changed against the files the plan names. Pure functions.

The build queue (plans/queue.py) decides overlap between builds from the files the plans name. A worker may still
change other files; a build that did is treated as touching the whole project from then on.
"""

from collections.abc import Iterable

from app.plans.model import Plan
from app.plans.queue import normalised

# YAAP's own folder in the project; what a build writes there is not part of the plan's work.
IGNORED_FOLDER = ".yaap"


def changed_paths(name_status: str) -> tuple[str, ...]:
    """Every path in `git diff --name-status -z` output: a rename or copy gives its old and its new path, a deletion
    its path. In order, without repeats."""
    fields = name_status.split("\0")
    paths: list[str] = []
    index = 0
    while index < len(fields):
        status = fields[index]
        if not status:
            index += 1
            continue
        count = 2 if status[0] in "RC" else 1
        paths.extend(fields[index + 1 : index + 1 + count])
        index += 1 + count
    return tuple(dict.fromkeys(path for path in paths if path))


def allowed_files(plan: Plan, number: int) -> frozenset[str] | None:
    """The normalised files of step `number` and the steps before it; None when one of them names no files (it may
    touch the whole project, so nothing is outside)."""
    files: set[str] = set()
    for step in plan.steps:
        if step.number > number:
            continue
        if not step.files:
            return None
        files.update(normalised(path) for path in step.files)
    return frozenset(files)


def ignored(path: str) -> bool:
    return path == IGNORED_FOLDER or path.startswith(IGNORED_FOLDER + "/")


def outside_plan(changed: Iterable[str], allowed: frozenset[str] | None) -> tuple[str, ...]:
    """The changed paths no allowed file or folder covers, normalised, in order and without repeats."""
    if allowed is None:
        return ()
    outside: list[str] = []
    for path in changed:
        text = literal_path(path)
        if not text or ignored(text) or text in outside:
            continue
        if not any(covered_by(text, file) for file in allowed):
            outside.append(text)
    return tuple(outside)


def literal_path(path: str) -> str:
    """A changed path as git names it, comparable with the plan's: forward slashes, no leading "./" or trailing "/".
    No glob is cut: a changed path is a real file name."""
    text = path.replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    return text.rstrip("/")


def covered_by(changed: str, allowed: str) -> bool:
    """Whether the allowed file or folder covers the changed path: the same path, or a folder above it. A folder
    named in the plan does not cover a file above it."""
    return not allowed or changed == allowed or changed.startswith(allowed + "/")

