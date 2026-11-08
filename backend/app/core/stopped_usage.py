"""The usage of a run that ended without the provider's final message: a reply that was stopped, failed on a
provider error or ended without one, or a sub-run cut short.

A reply or sub-run that finishes stores the usage its `Completed` reports. One that is stopped (the stop button, a
budget, a sub-run whose reply ended) or fails (a provider error, a stream without a final message) never gets that
message, so its usage is what its recorded model steps add up to: every model call that returned before the end,
with the tokens and cost the provider reported for it. A call that was still open reported nothing and counts for
nothing. A provider that reports usage only with its final message therefore leaves nothing to store.
"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.model_choice import Billing
from app.storage.models import RunStep, UsageRecord


@dataclass(frozen=True)
class StoppedRun:
    """Whose usage the steps of a run without a final message are, and how it is billed."""

    # The run record the model steps were recorded under; only its own steps count, as a sub-run stores its own.
    run_id: int
    environment_id: int
    conversation_id: int
    # The stored (incomplete or failed) reply; empty for a sub-run.
    message_id: int | None
    provider: str
    # The model when no step names one.
    model: str
    # main for a reply, worker for a sub-run.
    purpose: str
    billing: Billing


def stopped_usage(session: Session, run: StoppedRun) -> UsageRecord | None:
    """The usage of the run's recorded model calls; None when none returned."""
    row = session.execute(
        select(
            func.count(RunStep.id),
            func.coalesce(func.sum(RunStep.input_tokens), 0),
            func.coalesce(func.sum(RunStep.output_tokens), 0),
            func.coalesce(func.sum(RunStep.cache_read_tokens), 0),
            func.coalesce(func.sum(RunStep.cache_write_tokens), 0),
            func.sum(RunStep.cost),
            func.max(RunStep.model),
        ).where(RunStep.run_id == run.run_id, RunStep.kind == "model")
    ).one()
    calls, input_tokens, output_tokens, cache_read, cache_write, cost, model = row
    if not calls:
        return None
    return UsageRecord(
        environment_id=run.environment_id,
        conversation_id=run.conversation_id,
        message_id=run.message_id,
        provider=run.provider,
        model=model or run.model,
        purpose=run.purpose,
        input_tokens=int(input_tokens),
        output_tokens=int(output_tokens),
        cache_read_tokens=int(cache_read),
        cache_write_tokens=int(cache_write),
        # Each step is priced as it is recorded; a subscription's steps cost nothing per call.
        cost=float(cost) if cost is not None else None,
        billing=run.billing,
    )


def store_stopped_usage(session_factory: sessionmaker[Session], run: StoppedRun) -> None:
    with session_factory() as session:
        usage = stopped_usage(session, run)
        if usage is not None:
            session.add(usage)
            session.commit()
