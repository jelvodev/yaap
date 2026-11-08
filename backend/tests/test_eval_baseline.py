"""The baseline eval set (evals/baseline/, app/evals/baseline.py): the cases are valid, the build gives
the commits they start at, and every command and file check passes on the reference solution and, where the case
asks for a change, fails at the start. No model is involved: the solution comes from evals/baseline/solutions/.
"""

import asyncio
import shutil
import sys
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from app.evals.baseline import BASELINE_DIR, BaselineCommits, BaselineError, build_project, copy_mcp_server
from app.evals.cases import read_cases
from app.evals.checks import local_context, run_check
from app.evals.models import Check, CommandCheck, EvalCase, FileCheck
from app.evals.workspace import clone_at, remove_folder
from app.git.service import run_git

PROJECT = BASELINE_DIR / "project"
SOLUTIONS = BASELINE_DIR / "solutions"
NODE = shutil.which("node")

# case -> whether a check fails at the start (the case asks for a change). A case without a solution patch is
# answered in the reply only: its command and file checks hold at the start and stay true.
CASES = {
    "explain-reservations": False,
    "plain-question": False,
    "find-unused-functions": False,
    "fix-vat-rounding": True,
    "low-stock-command": True,
    "fix-failing-test": True,
    "tracker-csv-export": True,
    "move-discount-rules": True,
    "web-negative-money": True,
    "goal-csv-import": True,
}


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Path, BaselineCommits]]:
    folder = tmp_path_factory.mktemp("baseline") / "stockroom"
    commits = build_project(folder)
    yield folder, commits
    remove_folder(folder)


def baseline_cases() -> dict[str, EvalCase]:
    files = read_cases(PROJECT)
    assert all(file.case is not None for file in files), [(file.name, file.problems) for file in files]
    return {file.name: file.case for file in files if file.case is not None}


def runnable(check: Check) -> Check | None:
    """The check as this machine can run it: the cases call python3, as the runner does; node checks need Node.js."""
    if isinstance(check, FileCheck):
        return check
    if not isinstance(check, CommandCheck):
        return None
    if check.run.startswith("python3 "):
        return replace(check, run=f'"{sys.executable}" ' + check.run.removeprefix("python3 "))
    if check.run.startswith("node ") and NODE is None:
        return None
    return check


def statuses(case: EvalCase, workspace: Path) -> dict[str, str]:
    checks = [check for check in map(runnable, case.checks) if check is not None]
    context = local_context(workspace)

    async def run_all() -> dict[str, str]:
        return {check.name: (await run_check(case, check, context)).status for check in checks}

    return asyncio.run(run_all())


def test_the_baseline_has_ten_valid_cases_of_the_planned_kinds() -> None:
    cases = baseline_cases()
    assert set(cases) == set(CASES)
    assert all(case.budget is not None and case.budget.tokens for case in cases.values())
    assert {name for name, needs_change in CASES.items() if needs_change} == {
        path.stem for path in SOLUTIONS.glob("*.patch")
    }
    assert any(tool.startswith("tracker__") for tool in cases["tracker-csv-export"].allowed_tools or ())


def test_the_build_gives_the_commits_the_cases_start_at(built: tuple[Path, BaselineCommits]) -> None:
    folder, commits = built
    starts = {case.start.commit for case in baseline_cases().values()}
    assert starts == {commits.start, *commits.history}
    log = run_git(["log", "--format=%H %an %s"], folder).stdout.splitlines()
    assert [line.split(" ", 1)[0] for line in log] == [commits.cases, *reversed(commits.history), commits.start]
    assert run_git(["status", "--porcelain"], folder).stdout == ""
    assert (folder / ".blueprint" / "evals" / "goal-csv-import.md").is_file()


def test_the_build_refuses_a_folder_that_is_not_empty(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("mine", encoding="utf-8")
    with pytest.raises(BaselineError, match="not empty"):
        build_project(tmp_path)
    copied = copy_mcp_server(tmp_path / "mcp")
    assert sorted(path.name for path in copied) == ["issues.json", "tracker_server.py"]


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_checks_fail_at_the_start_and_pass_on_the_solution(
    name: str, built: tuple[Path, BaselineCommits], tmp_path: Path
) -> None:
    case = baseline_cases()[name]
    folder, _ = built
    workspace = tmp_path / name
    assert case.start.commit is not None
    clone_at(folder, workspace, case.start.commit)
    try:
        at_start = statuses(case, workspace)
        assert at_start, "the case has no command or file check to run here"
        if CASES[name]:
            assert "failed" in at_start.values(), at_start
            patch = SOLUTIONS / f"{name}.patch"
            run_git(["apply", "--whitespace=nowarn", str(patch)], workspace)
        else:
            assert set(at_start.values()) == {"passed"}, at_start
        solved = statuses(case, workspace)
        assert set(solved.values()) == {"passed"}, solved
    finally:
        remove_folder(workspace)
