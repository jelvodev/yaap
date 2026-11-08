"""The check before accepting.

A Blueprint proposal waits on a branch of its layer. Measuring it starts an eval comparison (44.3): the project's
cases in the normal setup and with the layer read at the proposal's branch, each played COMPARISON_REPEATS times.
The comparison is read back from the two runs whenever the proposal is shown. A project without eval cases shows the
proposal as *not measured*; it can be accepted all the same.

The same check, on demand, looks for rules that never make a difference (a proposal to remove one rule, measured the
same way) and for rules that contradict each other across levels (the utility model over the rules of every level;
a report only, nothing is stored or changed).
"""

import json
from dataclasses import dataclass
from typing import Literal, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.blueprint.layers import LayerKind, active_items
from app.core.utility import run_utility
from app.evals.compare import FINISHED, compare
from app.evals.runs import COMPARISON_REPEATS, EvalError, EvalService, result_plays
from app.evals.variants import Variant
from app.learning.proposals import Draft, RuleView, rule_views
from app.learning.service import NO_CASES, LearningError, LearningService, find_open
from app.storage.models import EvalResult, EvalRun, LearningProposal

MeasurementStatus = Literal["ready", "measuring", "measured", "not_measured", "not_needed"]
Effect = Literal["better", "worse", "same"]
CONTRADICTION_PROMPT = """Below are the rules an AI coding agent follows in one project, from three levels: global
(all work), environment (all projects of one environment) and project. Find pairs or groups of rules that contradict
each other, so that following one breaks another. Rules that only overlap or repeat each other are not
contradictions.

Answer with a JSON array only, no other text. Each item: {{"rules": [numbers of the rules], "explanation": "what
contradicts what, in one sentence"}}. Answer [] when nothing contradicts.

<rules>
{rules}
</rules>"""


@dataclass(frozen=True)
class Measurement:
    status: MeasurementStatus
    note: str | None
    pass_rate_without: float | None = None
    pass_rate_with: float | None = None
    # How the pass rate moved with the change, once both runs are finished.
    effect: Effect | None = None


@dataclass(frozen=True)
class Contradiction:
    rules: tuple[RuleView, ...]
    explanation: str


@dataclass(frozen=True)
class ContradictionReport:
    # False when no utility model answered; the report is then empty.
    available: bool
    contradictions: tuple[Contradiction, ...]


def effect_of(without: float | None, with_change: float | None) -> Effect | None:
    if without is None or with_change is None:
        return None
    if abs(with_change - without) < 1e-9:
        return "same"
    return "better" if with_change > without else "worse"


def measurement(session: Session, proposal: LearningProposal) -> Measurement:
    """The proposal's measurement as it stands: measuring turns into measured when both runs are finished."""
    status = cast(MeasurementStatus, proposal.measurement)
    if status != "measuring" or proposal.baseline_run_id is None or proposal.variant_run_id is None:
        return Measurement(status, proposal.measurement_note)
    runs = [session.get(EvalRun, proposal.baseline_run_id), session.get(EvalRun, proposal.variant_run_id)]
    if any(run is None for run in runs):
        return Measurement("not_measured", "The comparison's eval runs were removed; measure again.")
    results = [
        list(session.scalars(select(EvalResult).where(EvalResult.run_id == run_id)))
        for run_id in (proposal.baseline_run_id, proposal.variant_run_id)
    ]
    finished = all(run is not None and run.status not in ("queued", "running") for run in runs)
    played = all(result.status in FINISHED for side in results for result in side)
    comparison = compare(result_plays(results[0]), result_plays(results[1]))
    if not (finished and played):
        return Measurement("measuring", None, comparison.pass_rate_a, comparison.pass_rate_b)
    effect = effect_of(comparison.pass_rate_a, comparison.pass_rate_b)
    return Measurement("measured", None, comparison.pass_rate_a, comparison.pass_rate_b, effect)


def start_measuring(service: LearningService, evals: EvalService, project_id: int, proposal_id: int) -> None:
    """Start the comparison of the project's cases without and with the proposal. Runs in a worker thread."""
    with service.utility.session_factory() as session:
        proposal = find_open(session, project_id, proposal_id)
        if proposal.branch is None:
            raise LearningError(proposal.measurement_note or "This proposal has nothing to measure.")
        if proposal.measurement == "measuring" and measurement(session, proposal).status == "measuring":
            raise LearningError("The measurement is still going on.")
        variant = Variant(layer=cast(LayerKind, proposal.level), ref=proposal.branch)
        session.close()
    try:
        baseline, variant_run = evals.start_comparison(project_id, None, variant, COMPARISON_REPEATS)
    except EvalError as error:
        with service.utility.session_factory() as session:
            proposal = find_open(session, project_id, proposal_id)
            proposal.measurement, proposal.measurement_note = "not_measured", f"{error} {NO_CASES}"
            session.commit()
        raise LearningError(str(error)) from error
    with service.utility.session_factory() as session:
        proposal = find_open(session, project_id, proposal_id)
        proposal.measurement, proposal.measurement_note = "measuring", None
        proposal.baseline_run_id, proposal.variant_run_id = baseline, variant_run
        session.commit()


def project_rules(service: LearningService, project_id: int) -> list[RuleView]:
    """The rules of the standards of every level the project sees."""
    with service.utility.session_factory() as session:
        scope = service.scope(session, project_id)
    return rule_views(active_items(scope.layers))


def check_rule(service: LearningService, project_id: int, rule: RuleView) -> LearningProposal:
    """A proposal to remove one rule, on its branch, so a measurement can show whether the rule makes a difference."""
    if rule not in project_rules(service, project_id):
        raise LearningError("The project's levels have no such rule.")
    rationale = "Measures whether this rule makes a difference: the eval cases run with it and without it."
    draft = Draft("rule_removal", rule.level, rule.path, rule.rule, rationale, "check")
    created = service.store(project_id, None, [draft])
    if not created:
        raise LearningError("No proposal to remove this rule could be made; one may be waiting already.")
    return created[0]


def parse_contradictions(answer: str, rules: list[RuleView]) -> tuple[Contradiction, ...]:
    start, end = answer.find("["), answer.rfind("]")
    try:
        items = json.loads(answer[start : end + 1]) if start != -1 and end > start else []
    except json.JSONDecodeError:
        return ()
    found = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("rules"), list):
            continue
        numbers = [number for number in item["rules"] if isinstance(number, int) and 1 <= number <= len(rules)]
        explanation = " ".join(str(item.get("explanation") or "").split())[:500]
        if len(set(numbers)) >= 2 and explanation:
            found.append(Contradiction(tuple(rules[number - 1] for number in dict.fromkeys(numbers)), explanation))
    return tuple(found)


async def find_contradictions(service: LearningService, project_id: int) -> ContradictionReport:
    """Rules of different levels that contradict each other, by the utility model; a report only."""
    with service.utility.session_factory() as session:
        scope = service.scope(session, project_id)
    rules = rule_views(active_items(scope.layers))
    if len(rules) < 2:
        return ContradictionReport(True, ())
    listed = "\n".join(f"{number}. [{rule.level}] {rule.path}: {rule.rule}" for number, rule in enumerate(rules, 1))
    answer = await run_utility(
        service.utility,
        scope.environment_id,
        CONTRADICTION_PROMPT.format(rules=listed),
        max_tokens=1200,
        task="learning",
        project_id=project_id,
    )
    if answer is None:
        return ContradictionReport(False, ())
    return ContradictionReport(True, parse_contradictions(answer, rules))
