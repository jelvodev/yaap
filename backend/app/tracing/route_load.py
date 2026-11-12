"""Loading the route of a stored reply: its run tree, steps, notes and the helper calls made for it."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.storage.models import UsageRecord
from app.tracing.behind import ReplyFacts, reply_facts
from app.tracing.notes import clipped
from app.tracing.route import build_route
from app.tracing.route_facts import (
    RouteFacts,
    RouteStep,
    UtilityUse,
    WorkerRun,
    step_from_note,
    step_from_view,
)
from app.tracing.route_schema import RouteOut
from app.tracing.steps import RunNode, walk

WORKER_TASK_CHARS = 200


def worker_run(node: RunNode) -> WorkerRun:
    label = node.worker or node.agent or "Helper"
    return WorkerRun(
        run_id=node.id,
        label=label,
        started_at=node.started_at,
        finished_at=node.finished_at,
        status=node.status,
        task=clipped(node.task, WORKER_TASK_CHARS) if node.task else None,
    )


def stored_steps(facts: ReplyFacts) -> list[RouteStep]:
    whys = {call.step_id: call.why for call in facts.calls}
    steps = [step_from_view(view, whys.get(view.id)) for views in facts.steps.values() for view in views]
    return steps + [step_from_note(view) for view in facts.notes]


def utility_uses(session: Session, message_id: int) -> list[UtilityUse]:
    """The helper calls made for this reply (a title, memory suggestions, a summary), in the order they were made."""
    rows = session.scalars(
        select(UsageRecord)
        .where(UsageRecord.message_id == message_id, UsageRecord.purpose == "utility", UsageRecord.task.is_not(None))
        .order_by(UsageRecord.created_at, UsageRecord.id)
    )
    return [UtilityUse(row.task or "", row.model, row.cost, row.created_at, row.summary) for row in rows]


def stored_route_facts(session: Session, facts: ReplyFacts, message_id: int) -> RouteFacts:
    return RouteFacts(
        route=facts.route,
        main_run_id=facts.root.id,
        workers=[worker_run(node) for node in walk([facts.root]) if node.id != facts.root.id],
        steps=stored_steps(facts),
        afterwards=utility_uses(session, message_id),
        reply_status=facts.root.status,
    )


def reply_route(session: Session, conversation_id: int, message_id: int) -> RouteOut | None:
    """The route of a stored reply; None when the reply has no run."""
    facts = reply_facts(session, conversation_id, message_id)
    if facts is None:
        return None
    stops = build_route(stored_route_facts(session, facts, message_id))
    costs = [stop.cost for stop in stops if stop.cost is not None]
    root = facts.root
    duration = int((root.finished_at - root.started_at).total_seconds() * 1000) if root.finished_at else None
    return RouteOut(stops=stops, duration_ms=duration, cost=sum(costs) if costs else None)
