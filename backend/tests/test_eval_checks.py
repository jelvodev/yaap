"""Eval checks (app/evals/checks.py, rubric.py): each kind against a prepared workspace, and the rubric scorer."""

import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.other_vendor import other_vendor_models
from app.core.settings import SETTING_DEFINITIONS, store_setting
from app.core.utility import UtilityContext
from app.credentials.store import save_credential
from app.credentials.types import CredentialTestResult
from app.evals.checks import budget_overruns, budget_result, evaluate_case, local_context
from app.evals.models import (
    Budget,
    CaseUsage,
    CommandCheck,
    EvalCase,
    FileCheck,
    RubricCheck,
    ScorerCost,
    StartState,
)
from app.evals.rubric import (
    ModelRubricScorer,
    RubricRequest,
    RubricScorer,
    RubricVerdict,
    ScorerError,
    parse_verdict,
)
from app.providers.base import AgentRequest
from app.providers.catalog import ANTHROPIC_MODELS, OPENAI_MODELS
from app.providers.fake_provider import FakeProvider, ScriptedResponse
from app.providers.registry import ProviderRegistry, ProviderSpec
from app.storage.models import UsageRecord

PYTHON = f'"{sys.executable}"'


def case_with(*checks: CommandCheck | FileCheck | RubricCheck, budget: Budget | None = None) -> EvalCase:
    return EvalCase("fixture", "Fix the parser.", StartState(commit="3f2a1bc"), tuple(checks), budget=budget)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "dates.py").write_text("def parse_date(text):\n    return text\n", encoding="utf-8")
    return tmp_path


def evaluate(
    case: EvalCase, workspace: Path, usage: CaseUsage | None = None, scorer: RubricScorer | None = None
) -> tuple[str, list[tuple[str, str]]]:
    result = asyncio.run(evaluate_case(case, local_context(workspace, "", usage, scorer)))
    return result.status, [(check.status, check.detail) for check in result.checks]


def test_a_command_that_exits_with_zero_passes_and_another_fails(workspace):
    passing = CommandCheck("passes", "echo checked && exit 0", 30)
    failing = CommandCheck("fails", "exit 3", 30)

    status, checks = evaluate(case_with(passing, failing), workspace)

    assert status == "failed"
    assert checks[0][0] == "passed" and "checked" in checks[0][1]
    assert checks[1] == ("failed", "Exit code 3.")


def test_a_command_runs_in_the_workspace(workspace):
    command = CommandCheck("reads", f"{PYTHON} -c \"open('src/dates.py').read()\"", 30)

    assert evaluate(case_with(command), workspace)[0] == "passed"


def test_a_command_that_runs_too_long_is_stopped_and_fails(workspace):
    slow = CommandCheck("slow", f"{PYTHON} -c \"import time; time.sleep(20)\"", 0.5)

    status, checks = evaluate(case_with(slow), workspace)

    assert status == "failed"
    assert checks[0][1].startswith("Stopped after 0 s")


@pytest.mark.parametrize(
    ("check", "status", "detail"),
    [
        (FileCheck("f", "src/dates.py", contains=("def parse_date",)), "passed", "matches every condition"),
        (FileCheck("f", "src/dates.py", matches=(r"^\s+return text$",)), "passed", "matches every condition"),
        (FileCheck("f", "src/dates.py", contains=("isoweek",)), "failed", "does not contain 'isoweek'"),
        (FileCheck("f", "src/dates.py", not_contains=("return",)), "failed", "contains 'return'"),
        (FileCheck("f", "src/dates.py", matches=(r"^class ",)), "failed", "has no match for /^class /"),
        (FileCheck("f", "src/dates.py", not_matches=(r"return",)), "failed", "matches /return/ on line 2"),
        (FileCheck("f", "src/missing.py", contains=("x",)), "failed", "src/missing.py does not exist"),
        (FileCheck("f", "src/missing.py", exists=False), "passed", "does not exist, as expected"),
        (FileCheck("f", "src/dates.py", exists=False), "failed", "exists but should not"),
        (FileCheck("f", "src", contains=("x",)), "failed", "src does not exist"),
        (FileCheck("f", "../outside.py", contains=("x",)), "error", "outside the project folder"),
    ],
)
def test_file_content_must_or_must_not_match(workspace, check, status, detail):
    case_status, checks = evaluate(case_with(check), workspace)

    assert case_status == status
    assert checks[0][0] == status and detail in checks[0][1], checks[0][1]


@dataclass
class FakeScorer:
    verdict: RubricVerdict | None = None
    requests: list[RubricRequest] = field(default_factory=list)

    async def score(self, request: RubricRequest) -> RubricVerdict:
        self.requests.append(request)
        if self.verdict is None:
            raise ScorerError("The scorer's answer holds no verdict.")
        return self.verdict


def test_a_rubric_is_scored_with_the_reply_and_the_files_it_names(workspace):
    cost = ScorerCost("claude-haiku-5-5", 120, 30, 0.0004)
    scorer = FakeScorer(RubricVerdict(True, "The cause is named.", cost))
    rubric = RubricCheck("explains", "The reply names the cause.", ("src/dates.py", "src/gone.py"))

    result = asyncio.run(evaluate_case(case_with(rubric), local_context(workspace, "It was the week.", None, scorer)))

    assert result.status == "passed"
    assert result.checks[0].detail == "The cause is named." and result.checks[0].scorer_cost == cost
    assert result.scorer_cost_usd == 0.0004
    request = scorer.requests[0]
    assert (request.rubric, request.prompt) == ("The reply names the cause.", "Fix the parser.")
    assert request.reply == "It was the week."
    assert request.files == (
        ("src/dates.py", "def parse_date(text):\n    return text\n"),
        ("src/gone.py", "(this file does not exist)"),
    )


@pytest.mark.parametrize(
    ("scorer", "status", "detail"),
    [
        (FakeScorer(RubricVerdict(False, "The cause is not named.")), "failed", "The cause is not named."),
        (FakeScorer(None), "error", "holds no verdict"),
        (None, "error", "No scorer is available"),
    ],
)
def test_a_rubric_fails_or_errors_by_its_verdict(workspace, scorer, status, detail):
    rubric = RubricCheck("explains", "The reply names the cause.")

    case_status, checks = evaluate(case_with(rubric), workspace, scorer=scorer)

    assert case_status == status
    assert checks[0][0] == status and detail in checks[0][1]


USAGE = CaseUsage(steps=12, tokens=150_000, cost_usd=0.42, seconds=300)


@pytest.mark.parametrize(
    ("budget", "overruns"),
    [
        (Budget(steps=40, tokens=200_000, cost_usd=0.5, seconds=600), []),
        (Budget(steps=12, tokens=150_000, cost_usd=0.42, seconds=300), []),
        (Budget(steps=10), ["12 steps of 10 steps"]),
        (Budget(tokens=100_000), ["150,000 tokens of 100,000 tokens"]),
        (Budget(cost_usd=0.25, seconds=60), ["0.4200 USD of 0.2500 USD", "300 s of 60 s"]),
    ],
)
def test_the_budget_is_compared_with_the_reported_usage(budget, overruns):
    assert budget_overruns(budget, USAGE) == overruns
    assert budget_result(budget, USAGE).status == ("failed" if overruns else "passed")


def test_a_budget_without_reported_usage_cannot_be_judged(workspace):
    status, checks = evaluate(case_with(budget=Budget(steps=10)), workspace)

    assert status == "error"
    assert checks == [("error", "No usage was reported to compare with the budget.")]


def test_a_case_passes_only_when_every_check_passes(workspace):
    command = CommandCheck("passes", "exit 0", 30)
    content = FileCheck("f", "src/dates.py", contains=("def parse_date",))

    passing = evaluate(case_with(command, content, budget=Budget(steps=40)), workspace, usage=USAGE)
    over_budget = evaluate(case_with(command, content, budget=Budget(steps=5)), workspace, usage=USAGE)

    assert passing[0] == "passed"
    assert over_budget[0] == "failed" and over_budget[1][-1][0] == "failed"


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ('{"passed": true, "reason": "All points met."}', (True, "All points met.")),
        ('Here it is:\n```json\n{"passed": false, "reason": "No test."}\n```', (False, "No test.")),
        ('{"passed": true}', (True, "(no reason given)")),
    ],
)
def test_the_scorer_answer_is_read_as_a_verdict(answer, expected):
    assert parse_verdict(answer) == expected


@pytest.mark.parametrize("answer", ["Looks good to me.", '{"passed": "yes"}', '{"passed": true,'])
def test_an_answer_without_a_verdict_is_a_scorer_error(answer):
    with pytest.raises(ScorerError):
        parse_verdict(answer)


def test_other_vendors_are_listed_cheapest_first():
    models = [*ANTHROPIC_MODELS, *OPENAI_MODELS]

    others = other_vendor_models(models, "anthropic")

    assert sorted(others) == sorted(model.id for model in OPENAI_MODELS)
    prices = {m.id: m.pricing.standard.input + m.pricing.standard.output for m in OPENAI_MODELS}
    assert [prices[model] for model in others] == sorted(prices.values())
    assert len(other_vendor_models(models, "ollama")) == len(models)


@dataclass
class ScorerBackend:
    responses: list[ScriptedResponse] = field(default_factory=list)
    requests: list[AgentRequest] = field(default_factory=list)


def scorer_context(client, backend: ScorerBackend) -> UtilityContext:
    def create(_api_key: str | None) -> FakeProvider:
        return FakeProvider("unused", backend.responses, backend.requests)

    registry = ProviderRegistry(
        [
            ProviderSpec("anthropic", "Anthropic", "anthropic_api_key", create, runs_agents=True),
            ProviderSpec("openai", "OpenAI", "openai_api_key", create, runs_agents=True),
        ],
        [*ANTHROPIC_MODELS, *OPENAI_MODELS],
    )
    state = client.app.state
    return UtilityContext(state.session_factory, state.cipher, {}, registry)


def add_keys(client, *kinds: str) -> None:
    ok = CredentialTestResult(True, "The key works.")
    with client.app.state.session_factory() as session:
        for kind in kinds:
            save_credential(session, client.app.state.cipher, 1, kind, "sk-test-valid-key-1234", ok)


VERDICT = '{"passed": true, "reason": "The cause is named."}'


def score(client, vendor: str | None) -> tuple[RubricVerdict, ScorerBackend]:
    backend = ScorerBackend([ScriptedResponse(text=VERDICT)])
    scorer = ModelRubricScorer(scorer_context(client, backend), environment_id=1, vendor_under_test=vendor)
    request = RubricRequest("The reply names the cause.", "Fix the parser.", "It was the week.")
    return asyncio.run(scorer.score(request)), backend


def test_the_utility_model_scores_by_default_and_its_cost_is_kept_apart(client):
    add_keys(client, "anthropic_api_key")

    verdict, backend = score(client, None)

    assert (verdict.passed, verdict.reason) == (True, "The cause is named.")
    assert backend.requests[0].model == "claude-haiku-5-5"
    prompt = backend.requests[0].messages[0].content[0]["text"]
    assert "The reply names the cause." in prompt and "<outcome>" in prompt and "It was the week." in prompt
    assert verdict.cost is not None
    assert (verdict.cost.model, verdict.cost.input_tokens, verdict.cost.output_tokens) == ("claude-haiku-5-5", 10, 5)
    assert verdict.cost.cost_usd is not None and verdict.cost.cost_usd > 0
    with client.app.state.session_factory() as session:
        tasks = list(session.scalars(select(UsageRecord.task).where(UsageRecord.purpose == "utility")))
    assert tasks == ["eval_rubric"]


def test_a_model_of_another_vendor_scores_work_of_the_utility_model_vendor(client):
    add_keys(client, "anthropic_api_key", "openai_api_key")

    verdict, backend = score(client, "anthropic")

    assert backend.requests[0].model == other_vendor_models(OPENAI_MODELS, "anthropic")[0]
    assert verdict.cost is not None and verdict.cost.model == backend.requests[0].model


def test_the_utility_model_scores_when_the_policy_allows_no_other_vendor(client):
    add_keys(client, "anthropic_api_key", "openai_api_key")
    with client.app.state.session_factory() as session:
        store_setting(session, SETTING_DEFINITIONS["model_policy"], 1, {"connections": ["anthropic:api_key"]})

    verdict, backend = score(client, "anthropic")

    assert backend.requests[0].model == "claude-haiku-5-5"
    assert verdict.passed


def test_the_utility_model_scores_when_no_other_vendor_has_a_key(client):
    add_keys(client, "anthropic_api_key")

    _, backend = score(client, "anthropic")

    assert backend.requests[0].model == "claude-haiku-5-5"


def test_without_any_usable_scorer_model_the_rubric_cannot_be_scored(client):
    with pytest.raises(ScorerError, match="No model is available to score the rubric"):
        score(client, None)
