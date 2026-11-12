"""Who takes the next attempt at a delegated task, decided from the attempts so far.

A checkable task goes to the worker first; a failed check sends it back to the worker once with the failure, and a
second failure hands it to the main model. The decision is a pure function of the history so it can be tested as a
table and used by whatever runs the attempts.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

Role = Literal["worker", "main"]
CheckOutcome = Literal["passed", "failed", "not_run"]
AttemptOutcome = Literal["passed", "failed", "not_run", "stopped"]
NextAttempt = Literal["worker", "worker_retry", "main", "stop"]
MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class Attempt:
    by: Role
    outcome: AttemptOutcome


def next_attempt(history: Sequence[Attempt], first: Role) -> NextAttempt:
    """What to do after the attempts in `history`; `first` is who starts."""
    if not history:
        return first
    last = history[-1]
    if last.outcome != "failed" or last.by == "main" or len(history) >= MAX_ATTEMPTS:
        return "stop"
    failed_workers = sum(1 for attempt in history if attempt.by == "worker" and attempt.outcome == "failed")
    return "worker_retry" if failed_workers < 2 else "main"


def check_outcome(output: str, is_error: bool) -> CheckOutcome:
    """Read the result of the check command: only an exit code 0 counts as passed."""
    if is_error:
        # The gate refused, the user declined or the tool is not available: nothing was run.
        return "not_run"
    first_line = output.split("\n", 1)[0].rstrip()
    return "passed" if first_line == "Exit code 0" else "failed"


def sub_run_role(mode: Literal["work", "explore"], has_check: bool, asked: Role | None, by_brief: bool) -> Role:
    """Which model runs the sub-run: an explicit ask wins; briefed work without a check stays with the main model."""
    if asked is not None:
        return asked
    if by_brief and mode == "work" and not has_check:
        return "main"
    return "worker"
