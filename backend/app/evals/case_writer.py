"""Writing an eval case back to its file, `.blueprint/evals/<name>.md`.

`render_case` is the inverse of `cases.parse_case`: what it writes reads back as the same case. Quantities get their
unit (units.py refuses bare numbers), and a check's name is left out when it is the name the reader would give it
anyway, so a file stays as short as one written by hand. `save_case` writes a new file and commits only that file in
the project's Blueprint layer, the way every Blueprint change is committed (`blueprint.layers.commit`).
"""

import math
from dataclasses import dataclass

from app.blueprint.layers import MAX_FILE_BYTES, BlueprintError, Layer, commit
from app.blueprint.rules import render
from app.evals.cases import DEFAULT_COMMAND_TIMEOUT, EVALS_FOLDER, parse_case
from app.evals.models import Budget, Check, CommandCheck, EvalCase, FileCheck, StartState
from app.git.place import GitPlace, git_place
from app.git.service import GitError, Repository, run_git

SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600


class CaseWriteError(Exception):
    """The case cannot be saved as given; the message is safe to show and lists every problem."""


class CaseExistsError(CaseWriteError):
    """The project already has a case with this name."""


@dataclass(frozen=True)
class SavedCase:
    case: EvalCase
    # The file relative to the project folder, and the commit that added it.
    path: str
    commit: str


def render_case(case: EvalCase) -> str:
    """The case file's text: front matter in the order of the reader, then the prompt as the body."""
    front: dict[str, object] = {}
    if case.summary:
        front["summary"] = case.summary
    front["start"] = start_value(case.start)
    if case.agent:
        front["agent"] = case.agent
    if case.preset:
        front["preset"] = case.preset
    if case.allowed_tools is not None:
        front["allowed_tools"] = list(case.allowed_tools)
    if case.budget is not None:
        front["budget"] = budget_value(case.budget)
    if case.checks:
        front["checks"] = [check_value(check) for check in case.checks]
    return render(front, case.prompt)


def start_value(start: StartState) -> dict[str, object]:
    if start.checkpoint is not None:
        return {"checkpoint": start.checkpoint}
    return {"commit": start.commit or ""}


def budget_value(budget: Budget) -> dict[str, str]:
    value: dict[str, str] = {}
    if budget.steps is not None:
        value["steps"] = f"{budget.steps} steps"
    if budget.tokens is not None:
        value["tokens"] = tokens_text(budget.tokens)
    if budget.cost_usd is not None:
        value["cost"] = f"{number_text(budget.cost_usd)} USD"
    if budget.seconds is not None:
        value["time"] = seconds_text(budget.seconds)
    return value


def tokens_text(tokens: int) -> str:
    if tokens >= 1_000_000 and tokens % 1_000_000 == 0:
        return f"{tokens // 1_000_000}M tokens"
    if tokens >= 1_000 and tokens % 1_000 == 0:
        return f"{tokens // 1_000}k tokens"
    return f"{tokens} tokens"


def seconds_text(seconds: float) -> str:
    if seconds >= SECONDS_PER_HOUR and seconds % SECONDS_PER_HOUR == 0:
        return f"{int(seconds // SECONDS_PER_HOUR)} h"
    if seconds >= SECONDS_PER_MINUTE and seconds % SECONDS_PER_MINUTE == 0:
        return f"{int(seconds // SECONDS_PER_MINUTE)} min"
    return f"{number_text(seconds)} s"


def number_text(value: float) -> str:
    """A plain decimal without an exponent (units.py reads no `1e-05`); zero or less is written as given and
    refused by the reader."""
    if not math.isfinite(value):
        return str(value)
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def check_value(check: Check) -> dict[str, object]:
    if isinstance(check, CommandCheck):
        value: dict[str, object] = {"kind": "command", **named(check.name, f"command: {check.run}")}
        value["run"] = check.run
        if check.timeout_seconds != DEFAULT_COMMAND_TIMEOUT:
            value["timeout"] = seconds_text(check.timeout_seconds)
        return value
    if isinstance(check, FileCheck):
        value = {"kind": "file", **named(check.name, f"file: {check.path}"), "path": check.path}
        if not check.exists:
            value["exists"] = False
        for key in ("contains", "not_contains", "matches", "not_matches"):
            texts: tuple[str, ...] = getattr(check, key)
            if texts:
                value[key] = texts[0] if len(texts) == 1 else list(texts)
        return value
    first_line = check.rubric.strip().splitlines()[0] if check.rubric.strip() else ""
    value = {"kind": "rubric", **named(check.name, f"rubric: {first_line}"), "rubric": check.rubric}
    if check.files:
        value["files"] = list(check.files)
    return value


def named(name: str, default: str) -> dict[str, object]:
    """The check's name, unless it is empty or the one the reader derives (cases.py cuts it to 120 characters)."""
    name = name.strip()
    return {} if not name or name == default[:120] else {"name": name}


def case_repository(layer: Layer, environment_id: int) -> GitPlace:
    """Where git runs for the layer's repository: the environment's runner for a project in the workspace."""
    try:
        return git_place(environment_id, layer.repository)
    except GitError as error:
        raise CaseWriteError(f"The case could not be committed: {error}") from error


def save_case(layer: Layer, case: EvalCase, message: str | None = None, *, repository: Repository) -> SavedCase:
    """Write the case as a new file in the project's layer and commit only that file. Refuses a case that does not
    validate, a name the project already has, and a project that is not a git repository (nothing could be committed);
    nothing is left behind when committing fails. `repository` is the layer's repository where git runs; call this
    from a worker thread."""
    text = render_case(case)
    found = parse_case(case.name, text)
    if found.case is None:
        raise CaseWriteError(" ".join(found.problems))
    if len(text.encode("utf-8")) > MAX_FILE_BYTES:
        raise CaseWriteError(f"The case is larger than {MAX_FILE_BYTES} bytes.")
    if not (layer.repository / ".git").exists():
        raise CaseWriteError("The project is not a git repository, so the case cannot be committed.")
    relative = f"{EVALS_FOLDER}/{case.name}.md"
    file = layer.root / relative
    file.parent.mkdir(parents=True, exist_ok=True)
    try:
        with file.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except FileExistsError as error:
        raise CaseExistsError(f"The project already has an eval case named {case.name}.") from error
    try:
        commit(layer, relative, message or f"Add eval case {case.name}")
    except BlueprintError as error:
        file.unlink(missing_ok=True)
        raise CaseWriteError(f"The case could not be committed: {error}") from error
    return SavedCase(found.case, f"{layer.prefix}{relative}", head_commit(repository))


def head_commit(repository: Repository) -> str:
    """The commit just made."""
    try:
        return run_git(["rev-parse", "HEAD"], repository).stdout.strip()
    except GitError as error:
        raise CaseWriteError(f"The case was committed, but its commit cannot be read: {error}") from error
