"""An eval play's usage and its budget during the run.

`BudgetWatch` hears every step the trace recorder writes for the play's reply (also those of its sub-runs, which
share the recorder) and adds them up. The moment the usage goes over a limit of the case's budget it calls `stop`
once, which stops the reply as the stop button would, so an overrun costs at most the step that crossed the limit.
It also pins each model call's context snapshot while the play runs. The usage that is stored comes from the steps in
the database afterwards (`play_usage`), the same numbers the trace shows.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.evals.checks import budget_overruns
from app.evals.models import Budget, CaseUsage
from app.storage.models import AgentRunRecord, RunStep
from app.tracing.recorder import ModelStep, ToolStep


def step_tokens(step: ModelStep) -> int:
    usage = step.usage
    return usage.input_tokens + usage.output_tokens + usage.cache_read_tokens + usage.cache_write_tokens


class BudgetWatch:
    def __init__(
        self, budget: Budget | None, stop: Callable[[str], None], pin: Callable[[str], None] | None = None
    ) -> None:
        self.budget = budget
        self.stop = stop
        self.pin = pin
        self.started = time.monotonic()
        self.steps = 0
        self.tokens = 0
        self.cost = 0.0
        self.snapshots: set[str] = set()
        # What went over the budget, once it did.
        self.overrun: str | None = None

    def usage(self) -> CaseUsage:
        return CaseUsage(self.steps, self.tokens, round(self.cost, 6), time.monotonic() - self.started)

    def __call__(self, _run_id: int, step: ModelStep | ToolStep) -> None:
        self.steps += 1
        if isinstance(step, ModelStep):
            self.tokens += step_tokens(step)
            self.cost += step.cost or 0.0
            if step.snapshot is not None and step.snapshot not in self.snapshots:
                self.snapshots.add(step.snapshot)
                if self.pin is not None:
                    self.pin(step.snapshot)
        self.check()

    def check(self) -> None:
        """Stop the play once when its usage goes over the budget; also called when the time runs out."""
        if self.budget is None or self.overrun is not None:
            return
        overruns = budget_overruns(self.budget, self.usage())
        if overruns:
            self.overrun = "; ".join(overruns)
            self.stop(self.overrun)


@dataclass(frozen=True)
class PlayUsage:
    usage: CaseUsage
    # Calls refused because they would have asked for approval.
    asked: int


STEP_TOKENS = RunStep.input_tokens + RunStep.output_tokens + RunStep.cache_read_tokens + RunStep.cache_write_tokens


def play_usage(session: Session, conversation_id: int, seconds: float) -> PlayUsage:
    """What the play's reply used, from the steps of every run of its conversation (sub-runs included)."""
    runs = select(AgentRunRecord.id).where(AgentRunRecord.conversation_id == conversation_id)
    row = session.execute(
        select(
            func.count(RunStep.id),
            func.coalesce(func.sum(STEP_TOKENS), 0),
            func.coalesce(func.sum(RunStep.cost), 0.0),
            func.coalesce(func.sum(case((RunStep.approval == "asked", 1), else_=0)), 0),
        ).where(RunStep.run_id.in_(runs))
    ).one()
    steps, tokens, cost, asked = row
    return PlayUsage(CaseUsage(int(steps), int(tokens), round(float(cost), 6), seconds), int(asked))
