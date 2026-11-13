"""Which files a build step changed outside its plan: git's name-status output read, and the changed paths held
against the files of the step and the steps before it."""

import pytest

from app.plans.model import Plan, PlanStep
from app.plans.progress import MAX_OUTSIDE_PATHS, BuildInfo, build_info_from_json, build_info_to_json, with_outside
from app.plans.scope import allowed_files, changed_paths, outside_plan


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("", ()),
        ("M\0src/a.py\0", ("src/a.py",)),
        ("A\0new.py\0D\0old.py\0", ("new.py", "old.py")),
        ("R100\0src/old.py\0src/new.py\0", ("src/old.py", "src/new.py")),
        ("C075\0a.py\0b.py\0M\0c.py\0", ("a.py", "b.py", "c.py")),
        ("M\0a b.py\0", ("a b.py",)),
        ("M\0a.py\0M\0a.py\0", ("a.py",)),
        ("T\0link\0", ("link",)),
    ],
)
def test_changed_paths_reads_every_path_of_git_name_status_output(output: str, expected: tuple[str, ...]) -> None:
    assert changed_paths(output) == expected


@pytest.mark.parametrize(
    ("changed", "allowed", "expected"),
    [
        (("src/a.py",), {"src/a.py"}, ()),
        (("src/a.py", "src/b.py"), {"src/a.py"}, ("src/b.py",)),
        (("src/deep/a.py",), {"src"}, ()),
        (("src/a.py",), {"src/a.py/x"}, ("src/a.py",)),
        (("srcs/a.py",), {"src"}, ("srcs/a.py",)),
        (("README.md",), {"docs"}, ("README.md",)),
        (("anything.py",), {""}, ()),
        (("a.py",), None, ()),
        (("./src/a.py",), {"src/a.py"}, ()),
        ((r"src\a.py",), {"src/a.py"}, ()),
        (("src/a[1].py",), {"src/a[1].py"}, ()),
        ((".yaap/notes.md", ".yaap"), {"a.py"}, ()),
        ((".yaapx/notes.md",), {"a.py"}, (".yaapx/notes.md",)),
        (("sub/.yaap/x",), {"a.py"}, ("sub/.yaap/x",)),
        (("b.py", "b.py", ""), {"a.py"}, ("b.py",)),
        # A rename gives both paths: moving a plan's file out of its place is outside the plan.
        (("src/a.py", "lib/a.py"), {"src/a.py"}, ("lib/a.py",)),
        # A deletion is a change like any other.
        (("old.py",), {"a.py"}, ("old.py",)),
    ],
)
def test_outside_plan_is_every_changed_path_no_planned_file_or_folder_covers(
    changed: tuple[str, ...], allowed: set[str] | None, expected: tuple[str, ...]
) -> None:
    assert outside_plan(changed, None if allowed is None else frozenset(allowed)) == expected


PLAN = Plan(
    "t",
    steps=(
        PlanStep(1, "one", files=("./src/a.py",)),
        PlanStep(2, "two", files=("src/b/*.py",)),
        PlanStep(3, "three"),
        PlanStep(4, "four", files=("d.py",)),
    ),
)


@pytest.mark.parametrize(
    ("number", "expected"),
    [
        (1, frozenset({"src/a.py"})),
        (2, frozenset({"src/a.py", "src/b"})),
        (3, None),
        (4, None),
    ],
)
def test_a_step_may_change_its_own_files_and_those_of_the_steps_before_it(
    number: int, expected: frozenset[str] | None
) -> None:
    assert allowed_files(PLAN, number) == expected


def test_a_later_steps_files_are_outside_an_earlier_step() -> None:
    plan = Plan("t", steps=(PlanStep(1, "one", files=("a.py",)), PlanStep(2, "two", files=("b.py",))))
    assert outside_plan(("a.py", "b.py"), allowed_files(plan, 1)) == ("b.py",)


def test_the_recorded_paths_are_capped_and_survive_the_plan_body() -> None:
    paths = tuple(f"f{index}.py" for index in range(MAX_OUTSIDE_PATHS + 3))
    info = with_outside(BuildInfo(), 2, paths)
    assert info.whole_project
    assert info.outside_plan[0].paths == paths[:MAX_OUTSIDE_PATHS]
    assert info.outside_plan[0].total == MAX_OUTSIDE_PATHS + 3
    assert build_info_from_json({"build": build_info_to_json(info)}) == info
    assert with_outside(BuildInfo(), 2, ()) == BuildInfo()
    assert not BuildInfo().whole_project
