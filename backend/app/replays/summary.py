"""A replay beside its original: steps, tokens, cost and final answer of each side.

The original side counts the original reply's steps from the replayed model call on (sub-runs included), since the
replay starts there too; the replay side counts every step of the replay's reply. The final answer of each side is
its reply's text.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.search_index import message_text
from app.storage.models import AgentRunRecord, Message, RunStep
from app.tracing.steps import StepView, model_usage, run_trace, steps_cost, walk


@dataclass(frozen=True)
class SideSummary:
    run_id: int | None
    status: str | None
    steps: int
    model_calls: int
    tool_calls: int
    # Every token of the model calls: input, output and cache.
    tokens: int
    # None when no step had a cost (a subscription).
    cost: float | None
    final_answer: str | None


EMPTY = SideSummary(None, None, 0, 0, 0, 0, None, None)


def side_summary(session: Session, conversation_id: int, run_id: int | None, since: datetime | None) -> SideSummary:
    """The run's tree from since on (every step without since)."""
    if run_id is None:
        return EMPTY
    trace = run_trace(session, conversation_id, run_id)
    if trace is None:
        return EMPTY
    steps: list[StepView] = [
        step
        for node in walk([trace.root])
        for step in trace.steps.get(node.id, [])
        if since is None or step.started_at >= since
    ]
    usage = model_usage(steps)
    return SideSummary(
        run_id=run_id,
        status=trace.root.status,
        steps=len(steps),
        model_calls=sum(1 for step in steps if step.kind == "model"),
        tool_calls=sum(1 for step in steps if step.kind == "tool"),
        tokens=usage.input_tokens + usage.output_tokens + usage.cache_read_tokens + usage.cache_write_tokens,
        cost=steps_cost(steps),
        final_answer=final_answer(session, trace.root.message_id),
    )


def final_answer(session: Session, message_id: int | None) -> str | None:
    message = session.get(Message, message_id) if message_id is not None else None
    return message_text(message) if message is not None else None


def asked_count(session: Session, conversation_id: int) -> int:
    """Calls of the conversation's runs refused because they would have asked (an unattended replay)."""
    runs = select(AgentRunRecord.id).where(AgentRunRecord.conversation_id == conversation_id)
    query = select(func.count(RunStep.id)).where(RunStep.run_id.in_(runs), RunStep.approval == "asked")
    return session.scalar(query) or 0
