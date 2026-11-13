"""validate_plan accepts a usable plan and names every problem at once, each with the step it is about."""

from typing import Any

import pytest

from app.plans.model import (
    MAX_BODY,
    NO_RUN_WARNING,
    Plan,
    plan_from_json,
    plan_to_json,
    plan_warnings,
    validate_plan,
)


def step(**changes: Any) -> dict[str, Any]:
    return {"title": "Add the route", "checks": [{"text": "It answers", "run": "pytest -q"}], **changes}


def plan(**changes: Any) -> dict[str, Any]:
    return {"title": "Health endpoint", "steps": [step()], **changes}


def errors_of(arguments: dict[str, Any]) -> list[str]:
    checked = validate_plan(arguments)
    assert isinstance(checked, list), "the plan was accepted"
    return checked


def test_a_minimal_plan_is_accepted():
    checked = validate_plan(plan())
    assert isinstance(checked, Plan)
    assert checked.title == "Health endpoint"
    assert checked.steps[0].number == 1
    assert checked.steps[0].checks[0].run == "pytest -q"


def test_a_full_plan_keeps_every_field():
    checked = validate_plan(
        {
            "title": " Health endpoint ",
            "summary": "Add /health.",
            "risks": ["Port clash"],
            "steps": [
                step(details="Edit the router.", files=["backend/app/api/health.py"]),
                {"title": "Test it", "checks": ["Test passes", {"text": "Typed", "run": "mypy ."}]},
            ],
        }
    )
    assert isinstance(checked, Plan)
    assert checked.title == "Health endpoint"
    assert checked.risks == ("Port clash",)
    assert [item.number for item in checked.steps] == [1, 2]
    assert checked.steps[0].files == ("backend/app/api/health.py",)
    assert checked.steps[1].checks[0].run is None
    assert checked.steps[1].checks[1].run == "mypy ."


def test_a_plan_survives_storage_and_reading_back():
    checked = validate_plan(plan(summary="S", risks=["R"]))
    assert isinstance(checked, Plan)
    assert plan_from_json(plan_to_json(checked)) == checked


@pytest.mark.parametrize(
    ("arguments", "fragment"),
    [
        (plan(steps=[]), "no steps"),
        (plan(steps=[step() for _ in range(31)]), "31 steps"),
        (plan(steps=[step(checks=[])]), "Step 1 has no checks"),
        (plan(steps=[step(files=["/etc/passwd"])]), "absolute"),
        (plan(steps=[step(files=["../secret"])]), "outside the project"),
        (plan(steps=[step(files=["a/../../b"])]), "outside the project"),
        (plan(steps=[step(files=[r"C:Windowsx"])]), "absolute"),
        (plan(steps=[step(files=["C:" + chr(92) + "Windows" + chr(92) + "x"])]), "uses " + chr(92)),
        (plan(steps=[step(files=["a\0b"])]), "NUL"),
        (plan(title=""), "title is empty"),
        (plan(title="x" * 201), "title is over 200"),
        (plan(summary="x" * 2001), "summary is over 2000"),
        (plan(steps=[step(details="x" * 4001)]), "details is over 4000"),
        (plan(steps=[step(checks=[{"text": "x" * 301}])]), "text is over 300"),
        (plan(steps=[step(checks=[{"text": "ok", "run": "a\nb"}])]), "one command line"),
        (plan(steps=[step(checks=[{"text": "ok", "run": "x" * 501}])]), "run is over 500"),
        (plan(steps=[step(checks=[{"text": "ok"}] * 11)]), "more than 10 checks"),
        (plan(steps=[step(note="x" * 2001)]), "note is over 2000"),
        (plan(steps=["not a step"]), "Step 1 must have"),
        (plan(steps="nope"), "must be a list"),
    ],
)
def test_a_plan_with_this_problem_is_refused_with_a_sentence_naming_it(arguments, fragment):
    assert any(fragment in error for error in errors_of(arguments))


def test_a_body_over_the_size_limit_is_refused():
    steps = [step(details="x" * 3900) for _ in range(20)]
    assert any(str(MAX_BODY) in error for error in errors_of(plan(steps=steps)))


def test_every_problem_is_reported_at_once_and_names_its_step():
    errors = errors_of(
        plan(title="", steps=[step(checks=[]), step(), step(title="", files=["/abs"]), step(checks=[])])
    )
    assert any("title is empty" in error for error in errors)
    assert any(error.startswith("Step 1 has no checks") for error in errors)
    assert any(error.startswith("Step 3 title is empty") for error in errors)
    assert any(error.startswith("Step 3:") and "absolute" in error for error in errors)
    assert any(error.startswith("Step 4 has no checks") for error in errors)
    assert not any(error.startswith("Step 2") for error in errors)


@pytest.mark.parametrize(
    ("check", "warned"),
    [({"text": "A person looks"}, True), ({"text": "Tests", "run": "pytest"}, False), ({"text": "Blank", "run": " "}, True)],
)
def test_a_plan_without_a_command_yaap_can_run_is_accepted_with_a_warning(check, warned):
    checked = validate_plan(plan(steps=[step(checks=[check])]))
    assert isinstance(checked, Plan)
    assert (plan_warnings(checked) == [NO_RUN_WARNING]) is warned
