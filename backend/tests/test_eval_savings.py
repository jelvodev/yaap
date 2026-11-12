"""The context savings eval set (evals/savings/, the Freight project): the cases are valid, the build gives the
commits they start at, and every command and file check fails at the start and passes on the reference solution.
No model is involved: the solution comes from evals/savings/solutions/.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from app.evals.baseline import SAVINGS_DIR, BaselineCommits, build_project
from app.evals.cases import read_cases
from app.evals.models import EvalCase
from app.evals.workspace import clone_at, remove_folder
from app.git.service import run_git
from tests.test_eval_baseline import statuses

PROJECT = SAVINGS_DIR / "project"
SOLUTIONS = SAVINGS_DIR / "solutions"
CASES = ("adopt-carrier-surcharges", "fix-failing-quotes")


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Path, BaselineCommits]]:
    folder = tmp_path_factory.mktemp("savings") / "freight"
    commits = build_project(folder, SAVINGS_DIR)
    yield folder, commits
    remove_folder(folder)


def savings_cases() -> dict[str, EvalCase]:
    files = read_cases(PROJECT)
    assert all(file.case is not None for file in files), [(file.name, file.problems) for file in files]
    return {file.name: file.case for file in files if file.case is not None}


def test_the_savings_set_has_a_valid_case_and_a_solution_per_kind() -> None:
    cases = savings_cases()
    assert sorted(cases) == sorted(CASES)
    assert sorted(path.stem for path in SOLUTIONS.glob("*.patch")) == sorted(CASES)
    assert all(case.budget is not None and case.budget.tokens for case in cases.values())


def test_the_build_gives_the_commits_the_cases_start_at(built: tuple[Path, BaselineCommits]) -> None:
    folder, commits = built
    assert {case.start.commit for case in savings_cases().values()} == {commits.start, *commits.history}
    assert run_git(["log", "-1", "--format=%s", commits.start], folder).stdout.strip() == "Freight 1.0.0"
    assert run_git(["status", "--porcelain"], folder).stdout == ""


@pytest.mark.parametrize("name", CASES)
def test_the_checks_fail_at_the_start_and_pass_on_the_solution(
    name: str, built: tuple[Path, BaselineCommits], tmp_path: Path
) -> None:
    case = savings_cases()[name]
    folder, _ = built
    workspace = tmp_path / name
    assert case.start.commit is not None
    clone_at(folder, workspace, case.start.commit, 1)
    try:
        assert "failed" in statuses(case, workspace).values()
        run_git(["apply", "--whitespace=nowarn", str(SOLUTIONS / f"{name}.patch")], workspace)
        solved = statuses(case, workspace)
        assert set(solved.values()) == {"passed"}, solved
    finally:
        remove_folder(workspace)
