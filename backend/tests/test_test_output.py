"""Test output that keeps the failures and the summary: runner recognition, what is kept, and run_command."""

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from app.agent import command_tools
from app.agent.command_tools import run_command
from app.agent.outputs import STORE_ABOVE, OutputStore, full_output_note
from app.agent.runs import RunRegistry
from app.agent.test_output import FAILURE_BUDGET, SUMMARY_BUDGET, essentials, recognise_runner
from app.targets.base import CommandResult
from tests.test_agent_tools import start_run

FIXTURES = Path(__file__).parent / "fixtures" / "test_output"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    folder = tmp_path / "project"
    folder.mkdir()
    return folder


def fixture(name: str) -> str:
    return (FIXTURES / f"{name}.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("command", "runner"),
    [
        ("pytest", "pytest"),
        ("pytest -q tests/test_a.py::test_one", "pytest"),
        ("python -m pytest -x", "pytest"),
        ("python3 -X utf8 -m pytest", "pytest"),
        ('"C:\\Program Files\\Python\\python.exe" -m pytest tests', "pytest"),
        ("C:\\YAAP\\backend\\.venv\\Scripts\\python -m pytest -q", "pytest"),
        (".venv/bin/pytest", "pytest"),
        ("uv run pytest", "pytest"),
        ("uv run --quiet python -m pytest", "pytest"),
        ("poetry run pytest -q", "pytest"),
        ("cd backend && pytest -q", "pytest"),
        ("cd backend; python -m pytest", None),
        ("PYTHONPATH=. pytest", "pytest"),
        ("CI=1 FOO=bar python -m pytest", "pytest"),
        ("pytest -q | tail -20", None),
        ("npx vitest run", "vitest"),
        ("pnpm exec vitest run src", "vitest"),
        ("cd frontend && npx --yes vitest", "vitest"),
        ("npx jest --ci", "jest"),
        ("node_modules/.bin/jest", "jest"),
        ("go test ./...", "go"),
        ("go test -v -run TestAdd ./pkg", "go"),
        ("cargo test", "cargo"),
        ("cargo test --lib -- --nocapture", "cargo"),
        ("dotnet test", "dotnet"),
        ("dotnet test Shop.sln --no-build", "dotnet"),
        ("git pull && dotnet test", None),
        # Only a single invocation: with other commands the exit code and the output are not the test run's.
        ("python -m pytest -q; sed -n 1,40p a.py; sed -n 1,40p b.py", None),
        ("pytest || true", None),
        ("cd backend && pytest -q; sed -n 1,40p a.py", None),
        ("cd backend && pytest | tail -5", None),
        ("pytest & echo done", None),
        ("pytest\nsed -n 1,40p a.py", None),
        ("echo $(pytest)", None),
        ("  pytest -q  ", "pytest"),
        # Not a test run: a command that only mentions a runner, or starts another program.
        ("echo pytest", None),
        ("pip install pytest", None),
        ("python -m pip install pytest", None),
        ("uv add --dev pytest", None),
        ("grep -r vitest package.json", None),
        ("cat jest.config.js", None),
        ("npm install vitest", None),
        ("npm test", None),
        ("npm run test", None),
        ("go build ./...", None),
        ("go vet ./...", None),
        ("cargo build", None),
        ("dotnet build", None),
        ("python script.py pytest", None),
        ("python -m http.server", None),
        ("ls tests", None),
        ('echo "unbalanced', None),
        ("", None),
    ],
)
def test_a_test_command_is_recognised_by_its_runner(command, runner):
    assert recognise_runner(command) == runner


@pytest.mark.parametrize(
    ("runner", "passed", "summary"),
    [
        ("pytest", ["240 passed in 12.48s"], "240 passed in 12.48s"),
        ("vitest", ["Tests  324 passed (324)", "Test Files  24 passed (24)"], "Test Files  24 passed (24)"),
        ("jest", ["Tests:       324 passed, 324 total"], "Time:        4.218 s"),
        ("go", ["ok  \texample.com/shop/cart\t0.412s"], "ok  \texample.com/shop/pricing\t0.230s"),
        ("cargo", ["test result: ok. 120 passed; 0 failed"], "test result: ok. 0 passed"),
        ("dotnet", ["Passed!  - Failed:     0, Passed:   120"], "Duration: 41 ms"),
    ],
)
def test_a_passing_run_keeps_only_the_summary(runner, passed, summary):
    output = fixture(f"{runner}_pass")
    kept = essentials(runner, output, 0)
    assert kept is not None and len(kept) < len(output) / 5
    assert all(line in kept for line in passed) and summary in kept
    # No name of a passing test, no progress lines.
    assert "Case0" not in kept and "test_case_" not in kept and "Widget" not in kept and "PASS:" not in kept


@pytest.mark.parametrize(
    ("runner", "failing", "assertion", "summary"),
    [
        ("pytest", "test_total_with_tax", "assert Decimal('36.3000') == Decimal('36.30')", "2 failed, 238 passed"),
        ("pytest", "test_discount_never_negative", "assert Decimal('-4.00') >= 0", "2 failed, 238 passed"),
        ("vitest", "formats a negative amount", "expected '-€ 4,00' to be '€ -4,00'", "Tests  1 failed | 327"),
        ("jest", "formatAmount › rounds half up", 'Expected: "€ 1,01"', "Tests:       2 failed, 325 passed"),
        ("go", "TestTotalWithTax", "total with tax = 36.3000, want 36.30", "FAIL\texample.com/shop/cart"),
        ("go", "TestDiscountNeverNegative", "total = -4, want >= 0", "ok  \texample.com/shop/pricing"),
        ("cargo", "cart::tests::total_with_tax", "assertion `left == right` failed", "test result: FAILED. 118 passed"),
        ("dotnet", "Shop.Tests.CartTests.TotalWithTax", "Assert.Equal() Failure", "Failed!  - Failed:     2"),
    ],
)
def test_a_failing_run_keeps_the_failures_with_their_assertion_and_the_summary(runner, failing, assertion, summary):
    output = fixture(f"{runner}_fail")
    kept = essentials(runner, output, 1)
    assert kept is not None and len(kept) < len(output) / 2 and len(kept) < FAILURE_BUDGET + SUMMARY_BUDGET
    assert failing in kept and assertion in kept and summary in kept
    assert "Case0" not in kept and "test_case_" not in kept and "Widget" not in kept


def test_failures_beyond_the_budget_are_counted_not_kept():
    many = "".join(
        f"{'_' * 20} test_number_{number} {'_' * 20}\n\n    def test_number_{number}():\n"
        f">       assert {number} == 0\nE       assert {number} == 0\n\ntests/test_many.py:{number}: AssertionError\n"
        for number in range(1, 41)
    )
    output = f"{'=' * 20} FAILURES {'=' * 20}\n{many}{'=' * 20} 40 failed in 1.00s {'=' * 20}\n"
    kept = essentials("pytest", output, 1)
    assert kept is not None and len(kept) < FAILURE_BUDGET + SUMMARY_BUDGET
    assert "test_number_1 " in kept and "test_number_40" not in kept
    assert "more failures are in the full output." in kept and "40 failed in 1.00s" in kept


@pytest.mark.parametrize(
    ("runner", "output", "exit_code"),
    [
        ("pytest", "something else entirely\nline two", 1),
        ("pytest", "no tests ran in 0.01s", 5),
        ("pytest", "ERROR: file or directory not found: tests/nope.py", 4),
        ("vitest", "Error: Cannot find module 'vitest'", 1),
        ("jest", "sh: jest: command not found", 127),
        ("go", "# example.com/shop\n./cart.go:12:3: undefined: total", 1),
        ("cargo", "error[E0425]: cannot find value `x` in this scope", 101),
        ("dotnet", "MSBUILD : error MSB1009: Project file does not exist.", 1),
        # A summary but no failure details for a failing exit code.
        ("pytest", "== 3 failed in 0.1s ==", 1),
        ("go", "ok  \texample.com/shop/cart\t0.412s", 1),
        ("jest", "", 0),
        ("cargo", "", None),
    ],
)
def test_an_unknown_shape_gives_nothing_so_the_output_stays(runner, output, exit_code):
    assert essentials(runner, output, exit_code) is None


# run_command


class FakeExecutor:
    def __init__(self, result: CommandResult) -> None:
        self.result = result

    async def run(self, command, environment, timeout, session, silence=60, on_output=None):
        return self.result


def run_with(monkeypatch, project_folder, *, on: bool, command: str, result: CommandResult, secrets=None):
    run = start_run(RunRegistry(), project_folder)
    run.state.context_savings = on
    run.services = replace(
        run.services, outputs=OutputStore(project_folder.parent / "data", 3), secrets=secrets or {}
    )
    monkeypatch.setattr(command_tools, "executor", lambda _run: FakeExecutor(result))
    return run, asyncio.run(run_command(run, {"command": command}))


def test_a_long_failing_test_run_is_shortened_and_the_whole_output_stays_readable(monkeypatch, project):
    whole = fixture("pytest_fail")
    run, output = run_with(
        monkeypatch, project, on=True, command="cd backend && python -m pytest -q", result=CommandResult(1, whole)
    )
    assert len(whole) > STORE_ABOVE and len(output) < STORE_ABOVE
    assert output.startswith("Exit code 1\n[Test output shortened to the failures and the summary.]\n")
    assert "assert Decimal('-4.00') >= 0" in output and "2 failed, 238 passed" in output
    assert "test_case_001" not in output
    stored = run.outputs.read(1)
    assert stored == f"Exit code 1\n{whole.rstrip()}"
    assert output.endswith(full_output_note(stored, 1))


def test_a_long_passing_test_run_keeps_the_summary(monkeypatch, project):
    _, output = run_with(
        monkeypatch, project, on=True, command="go test -v ./...", result=CommandResult(0, fixture("go_pass"))
    )
    assert "ok  \texample.com/shop/cart" in output and "=== RUN" not in output and "[Full output: output 1," in output


def test_the_stored_output_never_holds_a_secret(monkeypatch, project):
    whole = fixture("pytest_fail").replace("tests/test_module_03.py:42", "tests/test_module_03.py:42 hunter2-token")
    run, output = run_with(
        monkeypatch,
        project,
        on=True,
        command="pytest",
        result=CommandResult(1, whole),
        secrets={"TOKEN": "hunter2-token"},
    )
    assert "hunter2-token" not in output and "hunter2-token" not in run.outputs.read(1)
    assert "[secret:TOKEN]" in run.outputs.read(1)


@pytest.mark.parametrize(
    ("on", "command", "result"),
    [
        (False, "pytest", CommandResult(1, fixture("pytest_fail"))),
        (True, "echo pytest", CommandResult(1, fixture("pytest_fail"))),
        (True, "pytest", CommandResult(1, fixture("pytest_fail")[-4000:])),
        (True, "pytest", CommandResult(1, "x" * 7000)),
        (True, "pytest", CommandResult(1, fixture("pytest_fail"), timed_out=True, stopped="total")),
    ],
)
def test_run_command_leaves_other_output_as_it_is(monkeypatch, project, on, command, result):
    run, output = run_with(monkeypatch, project, on=on, command=command, result=result)
    assert "[Test output shortened" not in output and run.outputs.read(1) is None
    if not result.timed_out:
        assert output == f"Exit code {result.exit_code}\n{result.output}".rstrip()
