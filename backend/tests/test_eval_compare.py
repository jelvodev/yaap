"""Comparing two eval runs: the pure comparison and the variant's validation."""

import pytest

from app.evals.compare import Play, compare, overall_rate, summarise, verdict
from app.evals.variants import Variant, VariantError, checked_variant, stored_variant


def play(case: str, status: str, cost: float = 0.1, tokens: int = 1000, steps: int = 10) -> Play:
    return Play(case, status, cost, tokens, steps)  # type: ignore[arg-type]  # test data names a PlayStatus


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([], "missing"),
        (["queued", "running"], "missing"),
        (["passed", "passed", "passed"], "passed"),
        (["passed", "failed", "passed"], "mixed"),
        (["failed", "error"], "failed"),
        (["passed", "error", "stopped"], "passed"),
        (["passed", "failed", "error"], "mixed"),
        (["error", "stopped"], "error"),
    ],
)
def test_the_verdict_over_the_repeats(statuses: list[str], expected: str) -> None:
    assert verdict(statuses) == expected


@pytest.mark.parametrize(
    ("statuses", "rate", "errors", "stopped", "cost", "expected_verdict"),
    [
        (["passed", "error", "stopped"], 1.0, 1, 1, 0.2, "passed"),
        (["passed", "failed"], 0.5, 0, 0, 0.2, "mixed"),
        (["error", "stopped"], None, 1, 1, None, "error"),
        ([], None, 0, 0, None, "missing"),
        (["failed", "error"], 0.0, 1, 0, 0.2, "failed"),
    ],
)
def test_errors_and_stops_stay_out_of_the_rate_and_the_medians(
    statuses: list[str], rate: float | None, errors: int, stopped: int, cost: float | None, expected_verdict: str
) -> None:
    plays = [play("c", status, 0.0 if status in ("error", "stopped") else 0.2) for status in statuses]

    side = summarise(plays)

    assert (side.pass_rate, side.errors, side.stopped, side.verdict) == (rate, errors, stopped, expected_verdict)
    assert side.median_cost == (pytest.approx(cost) if cost is not None else None)
    assert side.plays == sum(1 for status in statuses if status in ("passed", "failed"))
    assert overall_rate(plays) == rate
    assert (side.median_tokens is None) == (cost is None)


def test_two_fixed_result_sets_give_the_expected_differences() -> None:
    a = [
        play("dates", "passed", 0.10, 1000, 10),
        play("dates", "failed", 0.30, 3000, 30),
        play("dates", "passed", 0.20, 2000, 20),
        play("parser", "passed", 0.05, 500, 5),
        play("only-a", "error", 0.0, 0, 0),
        play("dates", "running", 9.0, 9, 9),  # Not finished: not counted.
    ]
    b = [
        play("dates", "passed", 0.08, 800, 8),
        play("dates", "passed", 0.12, 1200, 12),
        play("dates", "passed", 0.10, 1000, 10),
        play("parser", "failed", 0.07, 700, 9),
    ]

    result = compare(a, b)

    by_case = {row.case: row for row in result.cases}
    assert list(by_case) == ["dates", "only-a", "parser"]
    dates = by_case["dates"]
    assert (dates.a.verdict, dates.a.plays, dates.a.passed) == ("mixed", 3, 2)
    assert (dates.a.errors, dates.a.stopped) == (0, 0)
    assert dates.a.pass_rate == pytest.approx(2 / 3) and dates.b.pass_rate == 1.0
    assert dates.a.median_cost == pytest.approx(0.20) and dates.b.median_cost == pytest.approx(0.10)
    assert dates.cost_difference == pytest.approx(-0.10)
    assert dates.tokens_difference == -1000 and dates.steps_difference == -10
    parser = by_case["parser"]
    assert (parser.a.verdict, parser.b.verdict) == ("passed", "failed")
    assert parser.cost_difference == pytest.approx(0.02) and parser.steps_difference == 4
    only_a = by_case["only-a"]
    assert only_a.a.verdict == "error" and only_a.a.errors == 1 and only_a.b.verdict == "missing" and only_a.cost_difference is None
    assert result.pass_rate_a == pytest.approx(3 / 4) and result.pass_rate_b == pytest.approx(3 / 4)


@pytest.mark.parametrize(
    ("given", "problem"),
    [
        ({"layer": "project", "ref": None}, "both the layer and the git ref"),
        ({"layer": None, "ref": "proposal"}, "both the layer and the git ref"),
        ({"layer": "team", "ref": "proposal"}, "global, environment or project"),
        ({"layer": "project", "ref": "--upload-pack=x"}, "not a branch, tag or commit"),
        ({"layer": "project", "ref": "main..proposal"}, "not a branch, tag or commit"),
        ({"layer": "project", "ref": "main@{1}"}, "not a branch, tag or commit"),
    ],
)
def test_a_variant_is_refused_with_why(given: dict[str, str | None], problem: str) -> None:
    with pytest.raises(VariantError, match=problem):
        checked_variant(None, None, given["layer"], given["ref"])


def test_a_variant_is_stored_and_read_back() -> None:
    variant = checked_variant(" Fast ", "", "project", "rules/proposal")
    assert variant == Variant(preset="Fast", agent=None, layer="project", ref="rules/proposal")
    assert stored_variant(variant.as_json()) == variant
    assert variant.label == "preset Fast, project Blueprint at rules/proposal"
    assert Variant().as_json() is None and stored_variant(None).empty
