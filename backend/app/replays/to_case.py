"""An eval case from a replay: one action, no dialog.

The case starts at the replay's checkpoint (the files the replayed call saw), which is pinned for the case, so the
checkpoint pruning keeps it as long as the pin stands; the snapshots the replay used (the replayed call's and the
replay's own) are pinned too. The prompt is the replay's user message (the newest user text the call saw); the agent
and preset are the replay's; the checks are the project's test command when there is one and a rubric with the
replay's final answer as the reference; the budget is twice what the replay used. The file is written with
`evals/case_writer.py` into `.blueprint/evals/` as one Blueprint commit.
"""

import math
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.blueprint.levels import project_layer
from app.checkpoints.pins import case_owner, pin_checkpoint
from app.core.search_index import message_text
from app.core.settings import SETTING_DEFINITIONS, resolve_setting
from app.evals.case_writer import SavedCase, case_repository, save_case
from app.evals.cases import CASE_NAME, DEFAULT_COMMAND_TIMEOUT
from app.evals.from_conversation import existing_names, free_name, slug
from app.evals.models import Budget, Check, CommandCheck, EvalCase, RubricCheck, StartState
from app.evals.project_commands import find_test_command
from app.projects.service import project_path
from app.replays.summary import SideSummary, side_summary
from app.storage.models import AgentRunRecord, Checkpoint, Conversation, Message, Project, Replay, RunStep
from app.tracing.snapshots import SnapshotStore, trace_dir

BUDGET_HEADROOM = 2
REFERENCE_CHARS = 1500
FALLBACK_NAME = "replay"


class CaseFromReplayError(Exception):
    """The replay cannot become a case; the message is safe to show."""


@dataclass(frozen=True)
class CaseInputs:
    """What the case is made of, read from the replay."""

    prompt: str
    final_answer: str | None
    agent: str | None
    preset: str | None
    usage: SideSummary
    seconds: float


def make_case(
    session: Session, workspace_dir: Path, data_dir: Path, replay: Replay, inputs: CaseInputs, name: str | None
) -> SavedCase:
    """Write the case and pin its checkpoint and snapshots; refuses a replay that is not done or lost its checkpoint."""
    if replay.status != "done" or replay.conversation_id is None:
        raise CaseFromReplayError("Only a finished replay can become an eval case.")
    if replay.eval_case is not None:
        raise CaseFromReplayError(f"This replay is already the eval case {replay.eval_case}.")
    checkpoint = session.get(Checkpoint, replay.checkpoint_id) if replay.checkpoint_id is not None else None
    if checkpoint is None:
        raise CaseFromReplayError("The checkpoint of the replay's files is gone, so no case can start from it.")
    if not inputs.prompt:
        raise CaseFromReplayError("The replay has no prompt to play.")
    project = session.get_one(Project, replay.project_id)
    folder = project_path(workspace_dir, project)
    case = EvalCase(
        name=free_name(case_name(name, inputs.prompt), existing_names(folder)),
        prompt=inputs.prompt,
        start=StartState(checkpoint=checkpoint.id),
        checks=case_checks(session, project, folder, inputs),
        summary=f"Made from replay {replay.id} of step {replay.step_position}."[:200],
        agent=inputs.agent if inputs.agent and CASE_NAME.match(inputs.agent) else None,
        preset=inputs.preset,
        budget=case_budget(inputs.usage, inputs.seconds),
    )
    layer = project_layer(workspace_dir, project)
    message = f"Add eval case {case.name} from replay {replay.id}"
    saved = save_case(layer, case, message, repository=case_repository(layer, project.environment_id))
    owner = case_owner(project.id, saved.case.name)
    pin_checkpoint(session, checkpoint.id, owner)
    snapshots = SnapshotStore(trace_dir(data_dir))
    for digest in used_snapshots(session, replay):
        snapshots.pin(digest, owner)
    replay.eval_case = saved.case.name
    session.commit()
    return saved


def case_name(given: str | None, prompt: str) -> str:
    """The given name, else one from the prompt's first line."""
    return slug(given or "") or slug(prompt.splitlines()[0]) or FALLBACK_NAME


def case_checks(session: Session, project: Project, folder: Path, inputs: CaseInputs) -> tuple[Check, ...]:
    allowlist = resolve_setting(
        session, SETTING_DEFINITIONS["command_allowlist"], project.environment_id, project.id
    ).value
    entries = [entry for entry in allowlist if isinstance(entry, str)] if isinstance(allowlist, list) else []
    test = find_test_command([], entries, folder)
    checks: list[Check] = []
    if test is not None:
        checks.append(CommandCheck(f"command: {test.command}"[:120], test.command, DEFAULT_COMMAND_TIMEOUT))
    task = inputs.prompt.strip().splitlines()[0][:200]
    rubric = f"The work does what the prompt asks ({task}), completely and without unrelated changes."
    if inputs.final_answer:
        reference = inputs.final_answer.strip()[:REFERENCE_CHARS]
        rubric += f" It reaches at least the outcome of this reference answer:\n{reference}"
    checks.append(RubricCheck(f"rubric: {rubric.splitlines()[0]}"[:120], rubric))
    return tuple(checks)


def case_budget(usage: SideSummary, seconds: float) -> Budget | None:
    """Twice what the replay used, rounded up, as a case from a conversation gets."""
    if usage.steps == 0:
        return None
    tokens = math.ceil(usage.tokens * BUDGET_HEADROOM / 1000) * 1000 if usage.tokens else None
    cost = math.ceil(usage.cost * BUDGET_HEADROOM * 100) / 100 if usage.cost else None
    time = math.ceil(seconds * BUDGET_HEADROOM / 60) * 60 if seconds > 0 else None
    return Budget(steps=usage.steps * BUDGET_HEADROOM, tokens=tokens, cost_usd=cost, seconds=time)


def replay_prompt(session: Session, conversation_id: int) -> str:
    """The replay conversation's user message: the newest user text the replayed call saw."""
    message = session.scalar(
        select(Message)
        .where(Message.conversation_id == conversation_id, Message.role == "user")
        .order_by(Message.id)
        .limit(1)
    )
    return message_text(message) if message is not None else ""


def replay_seconds(replay: Replay) -> float:
    if replay.started_at is None or replay.finished_at is None:
        return 0.0
    return max((replay.finished_at - replay.started_at).total_seconds(), 0.0)


def used_snapshots(session: Session, replay: Replay) -> list[str]:
    """The replayed call's snapshot and those of every model call of the replay's runs."""
    digests = [replay.snapshot]
    if replay.conversation_id is not None:
        runs = select(AgentRunRecord.id).where(AgentRunRecord.conversation_id == replay.conversation_id)
        query = select(RunStep.snapshot).where(RunStep.run_id.in_(runs), RunStep.snapshot.is_not(None))
        digests += [digest for digest in session.scalars(query) if digest]
    return list(dict.fromkeys(digests))


def replay_agent_and_preset(session: Session, replay: Replay) -> tuple[str | None, str | None]:
    conversation = session.get(Conversation, replay.conversation_id) if replay.conversation_id is not None else None
    return (conversation.agent, conversation.preset) if conversation is not None else (None, None)


def case_from_replay(
    session: Session, workspace_dir: Path, data_dir: Path, replay: Replay, name: str | None
) -> SavedCase:
    """The one action: read the replay, write its case, pin what the case starts from."""
    if replay.conversation_id is None:
        raise CaseFromReplayError("Only a finished replay can become an eval case.")
    usage = side_summary(session, replay.conversation_id, replay.agent_run_id, None)
    agent, preset = replay_agent_and_preset(session, replay)
    inputs = CaseInputs(
        prompt=replay_prompt(session, replay.conversation_id),
        final_answer=usage.final_answer,
        agent=agent,
        preset=preset,
        usage=usage,
        seconds=replay_seconds(replay),
    )
    return make_case(session, workspace_dir, data_dir, replay, inputs, name)
