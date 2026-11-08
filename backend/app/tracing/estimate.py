"""A cost estimate from earlier similar runs: what a goal or a scheduled task's run is
likely to cost before it starts.

The estimate is a pure function of the totals of earlier runs (cost and tokens, from their steps). It gives the
median, what a run usually costs, and a high value, the 90th percentile by nearest rank: with fewer than ten earlier
runs that is the most expensive one, so the high value never claims more certainty than the history has. With fewer
than three earlier runs there is no estimate: one or two runs say too little.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

MIN_RUNS = 3
HIGH_PERCENTILE = 90


@dataclass(frozen=True)
class RunTotal:
    """What one earlier run used: its cost in US dollars (0 when not paid per token) and its tokens."""

    cost: float
    tokens: int


@dataclass(frozen=True)
class CostEstimate:
    runs: int
    median_cost: float
    high_cost: float
    median_tokens: int
    high_tokens: int


def median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def nearest_rank(values: Sequence[float], percentile: int) -> float:
    """The smallest value with at least percentile % of the values at or below it."""
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile / 100 * len(ordered)))
    return ordered[rank - 1]


def estimate(totals: Sequence[RunTotal]) -> CostEstimate | None:
    """The estimate from earlier runs' totals, or None when there are fewer than MIN_RUNS."""
    if len(totals) < MIN_RUNS:
        return None
    costs = [total.cost for total in totals]
    tokens = [float(total.tokens) for total in totals]
    return CostEstimate(
        runs=len(totals),
        median_cost=round(median(costs), 6),
        high_cost=round(nearest_rank(costs, HIGH_PERCENTILE), 6),
        median_tokens=round(median(tokens)),
        high_tokens=round(nearest_rank(tokens, HIGH_PERCENTILE)),
    )
