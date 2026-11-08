"""The monthly budgets during a reply: a reply stops when its usage reaches a budget.

When a reply starts, `confirm_pick` refuses it if a budget is already used up. A long agent run can still go far over
a budget after that, so every reply with a budget also gets a `BudgetGuard`: a step listener on its trace recorder
that adds up the cost of each model call (the sub-runs' too, as they share the recorder) on top of what the month had
spent when the reply started. The moment a budget is reached it asks the reply registry to stop the reply, as the stop
button would, so the call that crossed the budget is the last one.

`with_budget_stop` notes the stop where the user sees it: the reply's stored message says why it stopped, the
conversation's goal ends with the budget as its reason, and the usage of the stopped reply is stored, so the next
reply is refused at its start.
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.budget import BudgetLine, budget_lines, budget_stop_message, crossed_line
from app.core.goals import end_goal, open_goal
from app.storage.models import AgentRunRecord, Conversation, Message, RunStep, UsageRecord
from app.tracing.recorder import ModelStep, StepListener, ToolStep


class BudgetGuard:
    """Hears every step of one reply; stops the reply once when its cost reaches one of the budgets."""

    def __init__(self, lines: list[BudgetLine], stop: Callable[[], object]) -> None:
        self.lines = lines
        self.stop = stop
        self.used = 0.0
        # Why the reply was stopped, once it was.
        self.reason: str | None = None

    def __call__(self, _run_id: int, step: ModelStep | ToolStep) -> None:
        if self.reason is not None or not isinstance(step, ModelStep) or not step.cost:
            return
        self.used += step.cost
        line = crossed_line(self.lines, self.used)
        if line is not None:
            self.reason = budget_stop_message(line, self.used)
            self.stop()


def reply_budget_guard(session: Session, stop: Callable[[], object], environment_id: int) -> BudgetGuard | None:
    """A guard for a reply in the environment, or None when no budget applies to it."""
    lines = budget_lines(session, environment_id)
    return BudgetGuard(lines, stop) if lines else None


def both_listeners(first: StepListener | None, second: StepListener | None) -> StepListener | None:
    """One listener that tells both, in order; either may be absent."""
    if first is None or second is None:
        return first or second

    def listen(run_id: int, step: ModelStep | ToolStep) -> None:
        first(run_id, step)
        second(run_id, step)

    return listen


@dataclass(frozen=True)
class GuardedReply:
    """What noting a budget stop needs to know about the reply."""

    conversation_id: int
    # The reply's run record; its steps and those of its unfinished sub-runs are the stopped reply's usage.
    run_id: int
    billing: str


async def with_budget_stop(
    session_factory: sessionmaker[Session], guard: BudgetGuard, reply: GuardedReply, events: AsyncIterator[str]
) -> AsyncIterator[str]:
    """The reply's events; when the guard stopped the reply, the stop is noted after the reply stored what arrived."""
    try:
        async for event in events:
            yield event
    except asyncio.CancelledError:
        if guard.reason is not None:
            note_budget_stop(session_factory, reply, guard.reason)
        raise


def note_budget_stop(session_factory: sessionmaker[Session], reply: GuardedReply, reason: str) -> None:
    with session_factory() as session:
        conversation = session.get_one(Conversation, reply.conversation_id)
        head = session.get(Message, conversation.head_message_id) if conversation.head_message_id else None
        message_id = None
        if head is not None and head.role == "assistant" and head.status == "incomplete":
            head.error = reason
            message_id = head.id
        usage = stopped_usage(session, reply, conversation, message_id)
        if usage is not None:
            session.add(usage)
        goal = open_goal(session, reply.conversation_id)
        if goal is not None:
            end_goal(goal, "limit", reason)
        session.commit()


def stopped_usage(
    session: Session, reply: GuardedReply, conversation: Conversation, message_id: int | None
) -> UsageRecord | None:
    """The usage of a stopped reply, which writes none of its own: the model steps of its run and of the sub-runs the
    stop cut short (a finished sub-run stored its usage itself). None when it made no model call."""
    runs = select(AgentRunRecord.id).where(
        or_(
            AgentRunRecord.id == reply.run_id,
            (AgentRunRecord.parent_run_id == reply.run_id) & (AgentRunRecord.status == "running"),
        )
    )
    row = session.execute(
        select(
            func.count(RunStep.id),
            func.coalesce(func.sum(RunStep.input_tokens), 0),
            func.coalesce(func.sum(RunStep.output_tokens), 0),
            func.coalesce(func.sum(RunStep.cache_read_tokens), 0),
            func.coalesce(func.sum(RunStep.cache_write_tokens), 0),
            func.sum(RunStep.cost),
            func.max(RunStep.model),
        ).where(RunStep.run_id.in_(runs), RunStep.kind == "model")
    ).one()
    calls, input_tokens, output_tokens, cache_read, cache_write, cost, model = row
    if not calls:
        return None
    return UsageRecord(
        environment_id=conversation.environment_id,
        conversation_id=conversation.id,
        message_id=message_id,
        provider=conversation.provider,
        model=model or conversation.model,
        purpose="main",
        input_tokens=int(input_tokens),
        output_tokens=int(output_tokens),
        cache_read_tokens=int(cache_read),
        cache_write_tokens=int(cache_write),
        cost=float(cost) if cost is not None else None,
        billing=reply.billing,
    )
