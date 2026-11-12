"""Eval cases and runs of a project: the cases with their problems, starting a run or a
comparison, a run's progress and results, stopping and removing it, and two runs side by side."""

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import ProjectDependency, SessionDependency, WorkspaceDirDependency
from app.evals.cases import read_cases
from app.evals.compare import CaseComparison, SideSummary, Verdict, compare
from app.evals.models import Budget, CaseFile
from app.evals.runs import COMPARISON_REPEATS, MAX_REPEATS, EvalError, EvalService, RunStart, delete_run, result_plays
from app.evals.variants import Variant, VariantError, checked_variant, stored_variant
from app.projects.service import project_path
from app.storage.models import EvalResult, EvalRun

router = APIRouter(tags=["evals"])

RunStatus = Literal["queued", "running", "done", "stopped", "error"]
PlayStatus = Literal["queued", "running", "passed", "failed", "error", "stopped"]
CheckStatus = Literal["passed", "failed", "error"]
Trigger = Literal["panel", "command", "schedule"]
LayerKind = Literal["global", "environment", "project"]
RUNS_SHOWN = 50


class EvalCaseOut(BaseModel):
    name: str
    path: str
    valid: bool
    summary: str
    # The start state: a commit or a checkpoint number.
    start: str | None
    checks: int
    budget: str | None
    allowed_tools: list[str] | None
    problems: list[str]
    warnings: list[str]


class VariantIn(BaseModel):
    preset: str | None = Field(default=None, max_length=40)
    agent: str | None = Field(default=None, max_length=80)
    layer: LayerKind | None = None
    ref: str | None = Field(default=None, max_length=200)


class VariantOut(VariantIn):
    label: str


class EvalRunIn(BaseModel):
    # Only this case; empty plays every valid case.
    case_name: str | None = Field(default=None, max_length=60)
    repeats: int = Field(default=1, ge=1, le=MAX_REPEATS)
    variant: VariantIn | None = None


class ComparisonIn(BaseModel):
    case_name: str | None = Field(default=None, max_length=60)
    repeats: int = Field(default=COMPARISON_REPEATS, ge=1, le=MAX_REPEATS)
    variant: VariantIn


class EvalCheckOut(BaseModel):
    kind: Literal["command", "file", "rubric", "budget"]
    name: str
    status: CheckStatus
    detail: str
    scorer_cost: float | None = None
    scorer_model: str | None = None


class EvalResultOut(BaseModel):
    id: int
    case_name: str
    repeat: int
    status: PlayStatus
    checks: list[EvalCheckOut]
    detail: str | None
    model: str | None
    cost: float
    tokens: int
    steps: int
    seconds: float
    asked: int
    scorer_cost: float
    conversation_id: int | None
    agent_run_id: int | None
    # The kept workspace of a failed play, relative to the workspace, and until when it is kept.
    workspace: str | None
    workspace_expires_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None


class EvalRunOut(BaseModel):
    id: int
    project_id: int
    status: RunStatus
    trigger: Trigger
    case_name: str | None
    repeats: int
    variant: VariantOut | None
    baseline_run_id: int | None
    summary: str | None
    created_at: datetime
    finished_at: datetime | None
    plays: int
    passed: int
    failed: int
    errors: int
    finished: int
    cost: float


class EvalRunDetailOut(EvalRunOut):
    results: list[EvalResultOut]


class SideOut(BaseModel):
    verdict: Verdict
    plays: int
    passed: int
    errors: int
    stopped: int
    pass_rate: float | None
    median_cost: float | None
    median_tokens: float | None
    median_steps: float | None


class CaseComparisonOut(BaseModel):
    case: str
    a: SideOut
    b: SideOut
    cost_difference: float | None
    tokens_difference: float | None
    steps_difference: float | None


class ComparisonOut(BaseModel):
    a: EvalRunOut
    b: EvalRunOut
    cases: list[CaseComparisonOut]
    pass_rate_a: float | None
    pass_rate_b: float | None


def budget_text(budget: Budget | None) -> str | None:
    if budget is None:
        return None
    parts = []
    if budget.steps is not None:
        parts.append(f"{budget.steps} steps")
    if budget.tokens is not None:
        parts.append(f"{budget.tokens:,} tokens")
    if budget.cost_usd is not None:
        parts.append(f"{budget.cost_usd:.2f} USD")
    if budget.seconds is not None:
        parts.append(f"{budget.seconds:.0f} s")
    return ", ".join(parts) or None


def case_out(file: CaseFile) -> EvalCaseOut:
    case = file.case
    start = None
    if case is not None:
        start = f"commit {case.start.commit}" if case.start.commit else f"checkpoint {case.start.checkpoint}"
    return EvalCaseOut(
        name=file.name,
        path=file.path,
        valid=case is not None,
        summary=case.summary if case is not None else "",
        start=start,
        checks=len(case.checks) if case is not None else 0,
        budget=budget_text(case.budget) if case is not None else None,
        allowed_tools=list(case.allowed_tools) if case is not None and case.allowed_tools is not None else None,
        problems=list(file.problems),
        warnings=list(file.warnings),
    )


def variant_out(value: dict[str, Any] | None) -> VariantOut | None:
    variant = stored_variant(value)
    if variant.empty:
        return None
    return VariantOut(
        preset=variant.preset, agent=variant.agent, layer=variant.layer, ref=variant.ref, label=variant.label
    )


def result_out(row: EvalResult) -> EvalResultOut:
    return EvalResultOut(
        id=row.id,
        case_name=row.case_name,
        repeat=row.repeat,
        status=row.status,  # type: ignore[arg-type]  # stored as one of PlayStatus
        checks=[EvalCheckOut.model_validate(check) for check in row.checks or []],
        detail=row.detail,
        model=row.model,
        cost=row.cost,
        tokens=row.tokens,
        steps=row.steps,
        seconds=row.seconds,
        asked=row.asked,
        scorer_cost=row.scorer_cost,
        conversation_id=row.conversation_id,
        agent_run_id=row.agent_run_id,
        workspace=row.workspace,
        workspace_expires_at=row.workspace_expires_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


def run_out(run: EvalRun, results: list[EvalResult]) -> EvalRunOut:
    statuses = [result.status for result in results]
    return EvalRunOut(
        id=run.id,
        project_id=run.project_id,
        status=run.status,  # type: ignore[arg-type]  # stored as one of RunStatus
        trigger=run.trigger,  # type: ignore[arg-type]  # stored as one of Trigger
        case_name=run.case_name,
        repeats=run.repeats,
        variant=variant_out(run.variant),
        baseline_run_id=run.baseline_run_id,
        summary=run.summary,
        created_at=run.created_at,
        finished_at=run.finished_at,
        plays=len(results),
        passed=statuses.count("passed"),
        failed=statuses.count("failed"),
        errors=statuses.count("error") + statuses.count("stopped"),
        finished=sum(1 for value in statuses if value not in ("queued", "running")),
        cost=round(sum(result.cost for result in results), 6),
    )


def run_results(session: Session, run_id: int) -> list[EvalResult]:
    query = select(EvalResult).where(EvalResult.run_id == run_id).order_by(EvalResult.repeat, EvalResult.case_name)
    return list(session.scalars(query))


def find_run(session: Session, project_id: int, run_id: int) -> EvalRun:
    run = session.get(EvalRun, run_id)
    if run is None or run.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Eval run not found.")
    return run


def evals(request: Request) -> EvalService:
    return request.app.state.evals


def variant_of(payload: VariantIn | None) -> Variant:
    if payload is None:
        return Variant()
    try:
        return checked_variant(payload.preset, payload.agent, payload.layer, payload.ref)
    except VariantError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error


@router.get("/projects/{project_id}/evals/cases", response_model=list[EvalCaseOut])
def list_eval_cases(project: ProjectDependency, workspace_dir: WorkspaceDirDependency) -> list[EvalCaseOut]:
    """Every case file in the project's `.blueprint/evals/`, valid or not, with what is wrong with it."""
    return [case_out(file) for file in read_cases(project_path(workspace_dir, project))]


@router.get("/projects/{project_id}/evals/runs", response_model=list[EvalRunOut])
def list_eval_runs(project: ProjectDependency, session: SessionDependency) -> list[EvalRunOut]:
    """The project's most recent eval runs, newest first, with their progress."""
    runs = list(
        session.scalars(
            select(EvalRun).where(EvalRun.project_id == project.id).order_by(EvalRun.id.desc()).limit(RUNS_SHOWN)
        )
    )
    return [run_out(run, run_results(session, run.id)) for run in runs]


@router.post("/projects/{project_id}/evals/runs", response_model=EvalRunOut, status_code=status.HTTP_202_ACCEPTED)
def start_eval_run(
    payload: EvalRunIn, project: ProjectDependency, session: SessionDependency, request: Request
) -> EvalRunOut:
    """Start a run of every valid case, or of one, in the background."""
    variant = variant_of(payload.variant)
    project_id = project.id
    session.close()
    try:
        run_id = evals(request).start(RunStart(project_id, "panel", payload.case_name, payload.repeats, variant))
    except EvalError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    with request.app.state.session_factory() as fresh:
        return run_out(fresh.get_one(EvalRun, run_id), run_results(fresh, run_id))


@router.post(
    "/projects/{project_id}/evals/comparisons", response_model=list[EvalRunOut], status_code=status.HTTP_202_ACCEPTED
)
def start_eval_comparison(
    payload: ComparisonIn, project: ProjectDependency, session: SessionDependency, request: Request
) -> list[EvalRunOut]:
    """Start the normal setup and a variant side by side, each case played `repeats` times (three by default)."""
    variant = variant_of(payload.variant)
    project_id = project.id
    session.close()
    try:
        run_ids = evals(request).start_comparison(project_id, payload.case_name, variant, payload.repeats)
    except EvalError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    with request.app.state.session_factory() as fresh:
        return [run_out(fresh.get_one(EvalRun, run_id), run_results(fresh, run_id)) for run_id in run_ids]


@router.get("/projects/{project_id}/evals/runs/{run_id}", response_model=EvalRunDetailOut)
def read_eval_run(run_id: int, project: ProjectDependency, session: SessionDependency) -> EvalRunDetailOut:
    """A run with every play: its verdict, each check, usage, the conversation and run record it made."""
    run = find_run(session, project.id, run_id)
    results = run_results(session, run.id)
    return EvalRunDetailOut(**run_out(run, results).model_dump(), results=[result_out(row) for row in results])


@router.post("/projects/{project_id}/evals/runs/{run_id}/stop", status_code=status.HTTP_204_NO_CONTENT)
async def stop_eval_run(run_id: int, project: ProjectDependency, request: Request) -> Response:
    """Stop a run: the playing case's reply stops, the rest is not played. Nothing running is not an error."""
    await evals(request).stop(run_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/projects/{project_id}/evals/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_eval_run(run_id: int, project: ProjectDependency, session: SessionDependency, request: Request) -> Response:
    """Remove a finished run with its results, the conversations of its plays and their kept workspaces."""
    run = find_run(session, project.id, run_id)
    state = request.app.state
    try:
        delete_run(session, state.files_dir, state.workspace_dir, run)
    except EvalError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def side_out(side: SideSummary) -> SideOut:
    return SideOut(**side.__dict__)


def case_comparison_out(row: CaseComparison) -> CaseComparisonOut:
    return CaseComparisonOut(
        case=row.case,
        a=side_out(row.a),
        b=side_out(row.b),
        cost_difference=row.cost_difference,
        tokens_difference=row.tokens_difference,
        steps_difference=row.steps_difference,
    )


@router.get("/projects/{project_id}/evals/compare", response_model=ComparisonOut)
def compare_eval_runs(a: int, b: int, project: ProjectDependency, session: SessionDependency) -> ComparisonOut:
    """Two runs side by side: per case the verdict, pass rate over the repeats and median cost, and B minus A."""
    run_a, run_b = find_run(session, project.id, a), find_run(session, project.id, b)
    results_a, results_b = run_results(session, run_a.id), run_results(session, run_b.id)
    comparison = compare(result_plays(results_a), result_plays(results_b))
    return ComparisonOut(
        a=run_out(run_a, results_a),
        b=run_out(run_b, results_b),
        cases=[case_comparison_out(row) for row in comparison.cases],
        pass_rate_a=comparison.pass_rate_a,
        pass_rate_b=comparison.pass_rate_b,
    )
