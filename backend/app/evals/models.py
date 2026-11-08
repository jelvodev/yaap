"""The typed shape of an eval case and of its result.

A case is read from `.blueprint/evals/<name>.md` (cases.py); its checks run against a workspace (checks.py), and a
rubric is scored by a model behind the RubricScorer interface (rubric.py). Everything here is plain data.
"""

from dataclasses import dataclass, field
from typing import Literal

CheckKind = Literal["command", "file", "rubric", "budget"]
CheckStatus = Literal["passed", "failed", "error"]
CaseStatus = Literal["passed", "failed", "error"]
Billing = Literal["metered", "subscription"]


@dataclass(frozen=True)
class StartState:
    """Where the case's workspace starts: a commit of the project, or a checkpoint (pinned for the case) for a case
    made by replay. Exactly one of the two is set."""

    commit: str | None = None
    checkpoint: int | None = None


@dataclass(frozen=True)
class Budget:
    """Limits for one play of the case. None leaves that part open."""

    steps: int | None = None
    tokens: int | None = None
    cost_usd: float | None = None
    seconds: float | None = None


@dataclass(frozen=True)
class CommandCheck:
    """A command in the workspace that must exit with 0, such as the project's tests."""

    name: str
    run: str
    timeout_seconds: float
    kind: Literal["command"] = "command"


@dataclass(frozen=True)
class FileCheck:
    """A file in the workspace whose content must or must not match. With exists False, the file must be absent and
    no content condition is given."""

    name: str
    path: str
    exists: bool = True
    contains: tuple[str, ...] = ()
    not_contains: tuple[str, ...] = ()
    # Regular expressions, searched with re.MULTILINE.
    matches: tuple[str, ...] = ()
    not_matches: tuple[str, ...] = ()
    kind: Literal["file"] = "file"


@dataclass(frozen=True)
class RubricCheck:
    """A rubric a model scores the case's outcome against: the final reply and the listed files."""

    name: str
    rubric: str
    files: tuple[str, ...] = ()
    kind: Literal["rubric"] = "rubric"


Check = CommandCheck | FileCheck | RubricCheck


@dataclass(frozen=True)
class EvalCase:
    name: str
    prompt: str
    start: StartState
    checks: tuple[Check, ...]
    summary: str = ""
    agent: str | None = None
    preset: str | None = None
    # Tools that run without asking in an eval run; any other call that would ask is refused. None: no extra tools.
    allowed_tools: tuple[str, ...] | None = None
    budget: Budget | None = None


@dataclass(frozen=True)
class CaseFile:
    """One file in `.blueprint/evals/`: the case when it is valid, else what is wrong with it."""

    name: str
    path: str
    case: EvalCase | None
    problems: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class CaseUsage:
    """What one play of a case used, as the run that played it reports."""

    steps: int
    # Every token of the case's model calls: input, output and cache.
    tokens: int
    cost_usd: float
    seconds: float


@dataclass(frozen=True)
class ScorerCost:
    """What scoring a rubric cost; kept apart from the case's own usage."""

    model: str
    input_tokens: int
    output_tokens: int
    # In USD; None on a subscription, which has no cost per request.
    cost_usd: float | None
    billing: Billing = "metered"


@dataclass(frozen=True)
class CheckResult:
    kind: CheckKind
    name: str
    status: CheckStatus
    detail: str
    scorer_cost: ScorerCost | None = None


@dataclass(frozen=True)
class CaseResult:
    case: str
    status: CaseStatus
    checks: tuple[CheckResult, ...] = field(default=())

    @property
    def scorer_cost_usd(self) -> float:
        costs = (check.scorer_cost.cost_usd for check in self.checks if check.scorer_cost is not None)
        return round(sum(cost for cost in costs if cost is not None), 6)


def case_status(checks: tuple[CheckResult, ...]) -> CaseStatus:
    """A case passes when every check passes. A failed check fails it; a check that could not be judged, with none
    failed, makes it an error, since nothing says whether it would have passed."""
    statuses = {check.status for check in checks}
    if "failed" in statuses:
        return "failed"
    if "error" in statuses:
        return "error"
    return "passed"
