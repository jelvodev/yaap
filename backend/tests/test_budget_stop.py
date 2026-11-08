"""The monthly budget during a reply: a reply stops after the model call that reached the budget."""

import asyncio
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.core.budget import BudgetLine, crossed_line
from app.core.budget_stop import BudgetGuard
from app.providers.base import AgentEvent, AgentRequest, Completed, ModelCallFinished, TokenUsage
from app.providers.catalog import calculate_cost
from app.providers.fake_provider import ScriptedResponse
from app.storage.models import AgentRunRecord, Message, RunStep, UsageRecord
from app.tracing.provider import ModelTrace, traced_provider
from app.tracing.recorder import ModelStep, ToolStep
from tests.test_delegation import project_conversation
from tests.test_goals import wait_for_goal
from tests.test_tracing import MemoryRecorder

CALL = TokenUsage(input_tokens=100_000, output_tokens=5_000)


def model_step(cost: float | None) -> ModelStep:
    return ModelStep(position=1, started_at=datetime.now(UTC), duration_ms=1, model="m", usage=CALL, cost=cost)


def tool_step() -> ToolStep:
    return ToolStep(position=2, started_at=datetime.now(UTC), duration_ms=1, tool="read_file", summary="a.txt")


@pytest.mark.parametrize(
    ("lines", "used", "expected"),
    [
        ([], 100.0, None),
        ([BudgetLine("environment", "Private budget", 10.0, 4.0)], 5.99, None),
        ([BudgetLine("environment", "Private budget", 10.0, 4.0)], 6.0, "environment"),
        (
            [BudgetLine("environment", "Private budget", 10.0, 1.0), BudgetLine("total", "All", 5.0, 4.5)],
            0.5,
            "total",
        ),
    ],
)
def test_a_budget_is_crossed_when_the_month_and_the_reply_together_reach_it(lines, used, expected):
    line = crossed_line(lines, used)
    assert (line.scope if line else None) == expected


@pytest.mark.parametrize(
    ("costs", "stopped_after"),
    [
        ([0.4, 0.4, 0.4], 2),  # the second call reaches 0.8 of a 1.0 budget with 0.2 spent
        ([0.1, None, 0.1], None),  # a subscription call costs nothing
        ([0.9, 0.9], 1),
        ([], None),
    ],
)
def test_the_guard_stops_once_after_the_call_that_reached_the_budget(costs, stopped_after):
    stops: list[int] = []
    guard = BudgetGuard([BudgetLine("environment", "Private budget", 1.0, 0.2)], lambda: stops.append(len(stops)))
    seen = 0
    stop_at = None
    for cost in costs:
        guard(1, model_step(cost))
        guard(1, tool_step())
        seen += 1
        if stops and stop_at is None:
            stop_at = seen
    assert stop_at == stopped_after
    assert len(stops) == (1 if stopped_after else 0)
    assert (guard.reason is not None) == bool(stopped_after)


def call_cost(client, model: str = "claude-opus-5-5", usage: TokenUsage = CALL) -> float:
    info = client.app.state.providers.find_model(model)
    return calculate_cost(info, usage)


def set_budget(client, amount: float) -> None:
    response = client.put("/api/v1/environments/1/settings/budget_environment", json={"value": f"{amount:.6f}"})
    assert response.status_code == 200


def wait_until_idle(client, conversation_id: int, seconds: float = 8.0) -> None:
    deadline = time.monotonic() + seconds
    while conversation_id in client.get("/api/v1/environments/1/replies").json():
        assert time.monotonic() < deadline, "the reply still runs"
        time.sleep(0.05)


def three_calls() -> ScriptedResponse:
    return ScriptedResponse(
        text="Working through it.", tool_calls=(("list_files", {}),), call_usages=(CALL, CALL, CALL)
    )


def test_a_reply_that_reaches_the_budget_stops_after_that_call(client, fake):
    _, conversation_id = project_conversation(client)
    cost = call_cost(client)
    set_budget(client, cost * 1.5)
    fake.responses.append(three_calls())

    client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "A long job"})
    wait_until_idle(client, conversation_id)

    with client.app.state.session_factory() as session:
        run = session.scalars(select(AgentRunRecord)).one()
        model_steps = session.scalars(select(RunStep).where(RunStep.run_id == run.id, RunStep.kind == "model")).all()
        reply = session.scalars(select(Message).where(Message.role == "assistant")).one()
        usage = session.scalars(select(UsageRecord)).one()
    # The second call reached the budget: no third call was recorded.
    assert len(model_steps) == 2
    assert reply.status == "incomplete" and reply.error and "budget" in reply.error and "reached" in reply.error
    # The stopped reply's usage is stored, so the month's spending counts it.
    assert usage.message_id == reply.id and usage.cost == pytest.approx(2 * cost)
    shown = client.get(f"/api/v1/conversations/{conversation_id}").json()["messages"][-1]
    assert shown["status"] == "incomplete" and "budget" in shown["error"]

    refused = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Go on"})
    assert refused.status_code == 409 and "budget" in refused.json()["detail"]


def test_a_reply_within_the_budget_is_not_stopped(client, fake):
    _, conversation_id = project_conversation(client)
    set_budget(client, call_cost(client) * 10)
    fake.responses.append(three_calls())

    client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "A long job"})
    wait_until_idle(client, conversation_id)

    with client.app.state.session_factory() as session:
        reply = session.scalars(select(Message).where(Message.role == "assistant")).one()
        steps = session.scalars(select(RunStep).where(RunStep.kind == "model")).all()
    assert reply.status == "complete" and len(steps) == 3


def test_a_sub_runs_calls_count_towards_the_budget(client, fake):
    _, conversation_id = project_conversation(client)
    parent, worker = call_cost(client), call_cost(client, "claude-sonnet-5-5")
    set_budget(client, parent + worker / 2)
    fake.responses.extend(
        [
            ScriptedResponse(
                text="Delegated.", tool_calls=(("delegate", {"task": "Look around"}),), call_usages=(CALL, CALL)
            ),
            ScriptedResponse(text="Looked.", usage=CALL),
        ]
    )

    client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Delegate it"})
    wait_until_idle(client, conversation_id)

    with client.app.state.session_factory() as session:
        root = session.scalars(select(AgentRunRecord).where(AgentRunRecord.parent_run_id.is_(None))).one()
        parent_calls = session.scalars(select(RunStep).where(RunStep.run_id == root.id, RunStep.kind == "model")).all()
        reply = session.scalars(select(Message).where(Message.role == "assistant")).one()
        spent = sum(record.cost or 0 for record in session.scalars(select(UsageRecord)))
    # The worker's call reached the budget, so the parent made no second call.
    assert len(parent_calls) == 1
    assert reply.status == "incomplete" and reply.error and "budget" in reply.error
    assert spent == pytest.approx(parent + worker)


def test_a_goal_ends_with_the_budget_as_its_reason(client, fake):
    _, conversation_id = project_conversation(client)
    set_budget(client, call_cost(client) * 1.5)
    fake.responses.extend([three_calls(), ScriptedResponse(text="Never sent.")])

    client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "/goal finish the job"})
    goal = wait_for_goal(client, conversation_id, ("limit",))

    assert "budget" in goal["summary"] and goal["turns"] == 1
    time.sleep(0.3)
    assert len(fake.responses) == 1  # no second turn


class ThreeCalls:
    """A provider that makes three model calls in one stream and counts the calls it started."""

    def __init__(self) -> None:
        self.started = 0

    async def stream(self, request: AgentRequest) -> AsyncIterator[AgentEvent]:
        for _ in range(3):
            self.started += 1
            yield ModelCallFinished(request.model, CALL)
        yield Completed(stop_reason="end_turn", served_by_model=request.model, usage=CALL, provider_raw=[])

    async def close(self) -> None:
        return None


async def stop_on_the_second_step(provider: ThreeCalls, recorder: MemoryRecorder) -> None:
    task: asyncio.Task[None] | None = None

    def listen(_run_id: int, _step: ModelStep | ToolStep) -> None:
        if len(recorder.steps) == 2 and task is not None:
            task.cancel()

    recorder.listener = listen
    traced = traced_provider(provider, ModelTrace(recorder, 7, lambda _model, _usage: 1.0))

    async def consume() -> None:
        async for _event in traced.stream(AgentRequest("claude-opus-5-5", "medium", [], [])):
            pass

    task = asyncio.create_task(consume())
    with pytest.raises(asyncio.CancelledError):
        await task


def test_a_stop_asked_by_a_step_listener_lands_before_the_next_model_call():
    provider, recorder = ThreeCalls(), MemoryRecorder()
    asyncio.run(stop_on_the_second_step(provider, recorder))
    assert provider.started == 2 and len(recorder.steps) == 2
