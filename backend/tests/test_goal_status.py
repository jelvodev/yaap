"""A goal ends with one of the ended statuses; anything else is refused."""

from datetime import UTC, datetime
from typing import get_args

import pytest

from app.core.conversation_types import EndedGoalStatus, GoalStatus
from app.core.goals import end_goal
from app.storage.models import Goal


def active_goal() -> Goal:
    return Goal(
        conversation_id=1,
        objective="Ship it",
        status="active",
        max_turns=5,
        max_cost=1.0,
        turns=1,
        created_at=datetime.now(UTC),
    )


@pytest.mark.parametrize("status", get_args(EndedGoalStatus))
def test_each_ended_status_is_accepted_and_stamps_the_goal(status: str) -> None:
    goal = active_goal()
    end_goal(goal, status, "Summary")  # type: ignore[arg-type]  # the parameter is the Literal; the value comes from it
    assert goal.status == status
    assert goal.summary == "Summary"
    assert goal.finished_at is not None


@pytest.mark.parametrize("status", ["typo", "active", "paused", "DONE", "done ", ""])
def test_a_status_outside_the_ended_set_is_refused_and_leaves_the_goal_alone(status: str) -> None:
    goal = active_goal()
    with pytest.raises(ValueError, match="Not an ended goal status"):
        end_goal(goal, status, "Summary")  # type: ignore[arg-type]  # a wrong value is the point of the test
    assert goal.status == "active"
    assert goal.finished_at is None


def test_the_ended_statuses_are_a_subset_of_the_goal_statuses() -> None:
    assert set(get_args(EndedGoalStatus)) <= set(get_args(GoalStatus))
    assert not {"active", "paused"} & set(get_args(EndedGoalStatus))
