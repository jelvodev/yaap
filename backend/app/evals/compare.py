"""Two eval runs side by side: a pure function of their results.

Per case and per side: the verdict over its plays, the pass rate over the repeats and the median cost, tokens and
steps; then the difference of the medians (B minus A). One play of a model says little, so a comparison plays each
case three times by default and judges by the pass rate and the medians rather than by one play.
"""

from dataclasses import dataclass
from statistics import median
from typing import Literal

PlayStatus = Literal["queued", "running", "passed", "failed", "error", "stopped"]
Verdict = Literal["passed", "failed", "mixed", "error", "missing"]
FINISHED: frozenset[str] = frozenset({"passed", "failed", "error", "stopped"})


@dataclass(frozen=True)
class Play:
    case: str
    status: PlayStatus
    cost: float
    tokens: int
    steps: int


@dataclass(frozen=True)
class SideSummary:
    verdict: Verdict
    plays: int
    passed: int
    pass_rate: float | None
    median_cost: float | None
    median_tokens: float | None
    median_steps: float | None


@dataclass(frozen=True)
class CaseComparison:
    case: str
    a: SideSummary
    b: SideSummary
    # B minus A, of the medians; None when a side has no finished play.
    cost_difference: float | None
    tokens_difference: float | None
    steps_difference: float | None


@dataclass(frozen=True)
class Comparison:
    cases: tuple[CaseComparison, ...]
    pass_rate_a: float | None
    pass_rate_b: float | None


def verdict(statuses: list[str]) -> Verdict:
    if not statuses:
        return "missing"
    passed = statuses.count("passed")
    if passed == len(statuses):
        return "passed"
    if passed:
        return "mixed"
    return "failed" if "failed" in statuses else "error"


def middle(values: list[float]) -> float | None:
    return float(median(values)) if values else None


def summarise(plays: list[Play]) -> SideSummary:
    done = [play for play in plays if play.status in FINISHED]
    passed = sum(1 for play in done if play.status == "passed")
    return SideSummary(
        verdict=verdict([play.status for play in done]),
        plays=len(done),
        passed=passed,
        pass_rate=passed / len(done) if done else None,
        median_cost=middle([play.cost for play in done]),
        median_tokens=middle([float(play.tokens) for play in done]),
        median_steps=middle([float(play.steps) for play in done]),
    )


def difference(a: float | None, b: float | None, digits: int) -> float | None:
    return None if a is None or b is None else round(b - a, digits)


def overall_rate(plays: list[Play]) -> float | None:
    done = [play for play in plays if play.status in FINISHED]
    return sum(1 for play in done if play.status == "passed") / len(done) if done else None


def compare(a: list[Play], b: list[Play]) -> Comparison:
    """Every case either run played, by name."""
    rows = []
    for case in sorted({play.case for play in [*a, *b]}):
        side_a = summarise([play for play in a if play.case == case])
        side_b = summarise([play for play in b if play.case == case])
        rows.append(
            CaseComparison(
                case=case,
                a=side_a,
                b=side_b,
                cost_difference=difference(side_a.median_cost, side_b.median_cost, 6),
                tokens_difference=difference(side_a.median_tokens, side_b.median_tokens, 1),
                steps_difference=difference(side_a.median_steps, side_b.median_steps, 1),
            )
        )
    return Comparison(tuple(rows), overall_rate(a), overall_rate(b))
