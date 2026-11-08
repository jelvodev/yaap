"""The cost estimate from earlier similar runs: before a goal starts and on a scheduled task."""

from datetime import UTC, datetime, timedelta

import pytest

from app.storage.models import AgentRunRecord, Conversation, Goal, RunStep, ScheduledRun, ScheduledTask
from app.tracing.estimate import CostEstimate, RunTotal, estimate, median, nearest_rank
from tests.test_delegation import project_conversation

START = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


def totals(*costs: float) -> list[RunTotal]:
    return [RunTotal(cost, int(cost * 1000)) for cost in costs]


@pytest.mark.parametrize(
    ("history", "expected"),
    [
        ([], None),
        (totals(0.5, 0.7), None),
        (totals(0.3, 0.1, 0.2), CostEstimate(3, 0.2, 0.3, 200, 300)),
        (totals(0.4, 0.1, 0.3, 0.2), CostEstimate(4, 0.25, 0.4, 250, 400)),
        # With ten runs or more the high value is the 90th percentile, no longer the maximum.
        (totals(*[0.1 * n for n in range(1, 11)], 5.0), CostEstimate(11, 0.6, 1.0, 600, 1000)),
        # Runs on a subscription cost nothing; their tokens still give an estimate.
        ([RunTotal(0.0, 900), RunTotal(0.0, 100), RunTotal(0.0, 500)], CostEstimate(3, 0.0, 0.0, 500, 900)),
    ],
)
def test_the_estimate_is_the_median_and_the_90th_percentile_of_at_least_three_runs(history, expected):
    assert estimate(history) == expected


@pytest.mark.parametrize(
    ("values", "percentile", "expected"),
    [([5.0], 90, 5.0), ([1.0, 2.0, 3.0], 90, 3.0), ([1.0, 2.0, 3.0], 50, 2.0), (list(range(1, 21)), 90, 18)],
)
def test_nearest_rank(values, percentile, expected):
    assert nearest_rank(values, percentile) == expected


def test_median_of_an_even_count_is_the_mean_of_the_middle_two():
    assert median([4.0, 1.0, 3.0, 2.0]) == 2.5


def add_run(session, conversation: Conversation, at: datetime, costs: list[float], parent: int | None = None) -> int:
    """A run record at the moment, with a model step per cost (and a tool step, which costs nothing)."""
    record = AgentRunRecord(
        environment_id=conversation.environment_id,
        conversation_id=conversation.id,
        project_id=conversation.project_id,
        model="claude-opus-5-5",
        status="complete",
        approvals=[],
        started_at=at,
        parent_run_id=parent,
    )
    session.add(record)
    session.flush()
    for position, cost in enumerate(costs, start=1):
        session.add(
            RunStep(
                run_id=record.id, position=position, kind="model", started_at=at, input_tokens=1000, output_tokens=100,
                cost=cost,
            )
        )
    session.add(RunStep(run_id=record.id, position=len(costs) + 1, kind="tool", started_at=at, tool="read_file"))
    return record.id


def add_goal(session, conversation: Conversation, start: datetime, turn_costs: list[list[float]]) -> None:
    """An ended goal with a reply per turn; a sub-run under the first turn when it has more than one call."""
    goal = Goal(
        conversation_id=conversation.id, objective="x", status="done", max_turns=20, max_cost=5, turns=len(turn_costs),
        created_at=start, finished_at=start + timedelta(minutes=30),
    )
    session.add(goal)
    for turn, costs in enumerate(turn_costs):
        add_run(session, conversation, start + timedelta(minutes=turn + 1), costs)


def test_a_goal_estimate_comes_from_the_projects_earlier_goals_with_the_same_agent(client):
    project, conversation_id = project_conversation(client)
    _, elsewhere_id = project_conversation(client)
    path = f"/api/v1/projects/{project['id']}/goal-estimate"
    with client.app.state.session_factory() as session:
        conversation = session.get_one(Conversation, conversation_id)
        # Two goals: too few for an estimate.
        add_goal(session, conversation, START, [[0.10], [0.20]])
        add_goal(session, conversation, START + timedelta(days=1), [[0.50]])
        # Work outside a goal and an open goal do not count.
        add_run(session, conversation, START + timedelta(days=1, hours=5), [9.0])
        session.add(Goal(conversation_id=conversation.id, objective="y", status="active", max_turns=20, max_cost=5,
                         turns=1, created_at=START + timedelta(days=2)))
        session.commit()
    assert client.get(path).json() == {
        "runs": 2, "min_runs": 3, "median_cost": None, "high_cost": None, "median_tokens": None, "high_tokens": None,
    }

    with client.app.state.session_factory() as session:
        conversation = session.get_one(Conversation, conversation_id)
        open_goal = session.query(Goal).filter(Goal.status == "active").one()
        open_goal.status, open_goal.finished_at = "blocked", START + timedelta(days=2, minutes=30)
        first = add_run(session, conversation, START + timedelta(days=2, minutes=1), [0.40])
        add_run(session, conversation, START + timedelta(days=2, minutes=2), [0.60], parent=first)  # a sub-run
        # Goals of another project, of another agent and of a scheduled task do not count.
        add_goal(session, session.get_one(Conversation, elsewhere_id), START, [[7.0]])
        other_agent = Conversation(environment_id=1, project_id=project["id"], title="o", provider="anthropic",
                                   model="claude-opus-5-5", effort="medium", agent="reviewer")
        scheduled = Conversation(environment_id=1, project_id=project["id"], title="s", provider="anthropic",
                                 model="claude-opus-5-5", effort="medium", scheduled_task_id=1)
        session.add_all([other_agent, scheduled])
        session.flush()
        add_goal(session, other_agent, START, [[8.0]])
        add_goal(session, scheduled, START, [[9.0]])
        session.commit()

    found = client.get(path).json()
    # Goal totals: 0.30, 0.50 and 1.00 (its reply and its sub-run).
    assert found["runs"] == 3
    assert found["median_cost"] == pytest.approx(0.5) and found["high_cost"] == pytest.approx(1.0)
    assert found["median_tokens"] == 2200 and found["high_tokens"] == 2200
    reviewer = client.get(path, params={"agent": "reviewer"}).json()
    assert reviewer["runs"] == 1 and reviewer["median_cost"] is None


def test_a_task_estimate_comes_from_the_tasks_finished_runs(client):
    project, conversation_id = project_conversation(client)
    with client.app.state.session_factory() as session:
        task = ScheduledTask(
            project_id=project["id"], name="Nightly", prompt="Tidy", model="claude-opus-5-5", effort="medium",
            schedule_kind="manual", max_turns=5, max_cost=2, conversation_mode="one", running=False,
        )
        session.add(task)
        session.flush()
        conversation = session.get_one(Conversation, conversation_id)
        for day, costs in enumerate([[0.2], [0.1, 0.1], [0.6], []]):
            start = START + timedelta(days=day)
            session.add(ScheduledRun(task_id=task.id, conversation_id=conversation_id, trigger="schedule",
                                     status="done", started_at=start, finished_at=start + timedelta(minutes=10)))
            if costs:
                add_run(session, conversation, start + timedelta(minutes=1), costs)
        # A run that still goes on does not count.
        running = START + timedelta(days=9)
        session.add(ScheduledRun(task_id=task.id, conversation_id=conversation_id, trigger="manual",
                                 status="running", started_at=running))
        add_run(session, conversation, running + timedelta(minutes=1), [5.0])
        session.commit()
        task_id = task.id

    found = client.get(f"/api/v1/projects/{project['id']}/tasks/{task_id}/estimate").json()
    # The run without a model call says nothing about cost.
    assert found["runs"] == 3
    assert found["median_cost"] == pytest.approx(0.2) and found["high_cost"] == pytest.approx(0.6)
    assert client.get(f"/api/v1/projects/{project['id']}/tasks/999/estimate").status_code == 404
