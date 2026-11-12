"""Who takes the next attempt at a delegated task."""

import pytest

from app.agent.escalation import (
    Attempt,
    AttemptOutcome,
    NextAttempt,
    Role,
    check_outcome,
    next_attempt,
    sub_run_role,
)

W_FAIL = Attempt("worker", "failed")
M_FAIL = Attempt("main", "failed")


@pytest.mark.parametrize(
    ("history", "first", "expected"),
    [
        ([], "worker", "worker"),
        ([], "main", "main"),
        ([Attempt("worker", "passed")], "worker", "stop"),
        ([Attempt("main", "passed")], "main", "stop"),
        ([Attempt("worker", "not_run")], "worker", "stop"),
        ([Attempt("worker", "stopped")], "worker", "stop"),
        ([Attempt("main", "stopped")], "main", "stop"),
        ([W_FAIL], "worker", "worker_retry"),
        ([W_FAIL, Attempt("worker", "passed")], "worker", "stop"),
        ([W_FAIL, Attempt("worker", "not_run")], "worker", "stop"),
        ([W_FAIL, Attempt("worker", "stopped")], "worker", "stop"),
        ([W_FAIL, W_FAIL], "worker", "main"),
        ([W_FAIL, W_FAIL, Attempt("main", "passed")], "worker", "stop"),
        ([W_FAIL, W_FAIL, M_FAIL], "worker", "stop"),
        ([W_FAIL, W_FAIL, W_FAIL], "worker", "stop"),
        ([M_FAIL], "main", "stop"),
        ([M_FAIL, W_FAIL, W_FAIL], "main", "stop"),
    ],
)
def test_next_attempt_follows_the_history(history: list[Attempt], first: Role, expected: NextAttempt) -> None:
    assert next_attempt(history, first) == expected


@pytest.mark.parametrize("outcome", ["passed", "failed", "not_run", "stopped"])
def test_next_attempt_after_a_main_attempt_always_stops(outcome: AttemptOutcome) -> None:
    assert next_attempt([Attempt("main", outcome)], "main") == "stop"


@pytest.mark.parametrize(
    ("output", "is_error", "expected"),
    [
        ("Exit code 0\nall good", False, "passed"),
        ("Exit code 0", False, "passed"),
        ("Exit code 0   \nok", False, "passed"),
        ("Exit code 0\r\nok", False, "passed"),
        ("Exit code 1\n1 failed", False, "failed"),
        ("Exit code 10", False, "failed"),
        ("Exit code 01", False, "failed"),
        ("Command timed out after 120 seconds", False, "failed"),
        ("", False, "failed"),
        ("ok\nExit code 0", False, "failed"),
        ("Exit code 0", True, "not_run"),
        ("Refused by the permission gate", True, "not_run"),
        ("", True, "not_run"),
    ],
)
def test_check_outcome_passes_only_on_exit_code_zero(output: str, is_error: bool, expected: str) -> None:
    assert check_outcome(output, is_error) == expected


@pytest.mark.parametrize(
    ("mode", "has_check", "asked", "by_brief", "expected"),
    [
        ("work", True, None, True, "worker"),
        ("work", False, None, True, "main"),
        ("work", False, None, False, "worker"),
        ("work", True, None, False, "worker"),
        ("explore", False, None, True, "worker"),
        ("explore", True, None, True, "worker"),
        ("explore", False, None, False, "worker"),
        ("work", False, "worker", True, "worker"),
        ("work", True, "main", True, "main"),
        ("explore", False, "main", False, "main"),
        ("work", False, "main", False, "main"),
    ],
)
def test_sub_run_role_sends_only_checkable_work_to_the_worker(
    mode: str, has_check: bool, asked: Role | None, by_brief: bool, expected: Role
) -> None:
    assert sub_run_role(mode, has_check, asked, by_brief) == expected  # type: ignore[arg-type]  # str from the table
