"""Playing one eval case once (plan choices 2 and 3).

A play makes the case's throwaway workspace (workspace.py), opens an archived conversation in the project for it,
and starts the case's prompt through the reply service as an unattended reply: nobody is there to approve, so a call
that would ask is refused (and counted as *asked*); the case's `allowed_tools` run without asking, as a scheduled
task's allowlists do. The reply works in the workspace instead of the project folder, with the commands in the
environment's runner, which holds only the environment's folder. Its steps are watched against the budget while it
runs (budget.py). Then the case's checks run in the same workspace, the result is stored, and a passed play's
workspace is removed.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import anyio
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.definitions import agent_part, find_agent
from app.blueprint.context import build_system_parts
from app.blueprint.layers import Layer
from app.core.background import BackgroundWork
from app.core.chat_history import add_user_message
from app.core.replies import ReplyJob
from app.core.reply_errors import ReplyError
from app.core.reply_service import ReplyRequest, ReplyService
from app.core.reply_services import ReplyServices
from app.core.reply_setup import ReplySetup
from app.core.search_index import message_text
from app.evals.budget import BudgetWatch, PlayUsage, play_usage
from app.evals.checks import CheckContext, evaluate_case
from app.evals.models import CaseResult, CheckResult, EvalCase
from app.evals.rubric import ModelRubricScorer
from app.evals.variants import Variant
from app.evals.workspace import KEEP_FAILED, WorkspaceError, make_workspace, play_name, remove_workspace, run_folder
from app.presets.service import default_preset, find_preset_by_name
from app.projects.service import project_path
from app.providers.base import SystemPart
from app.runner.service import RUNNERS, runner_path
from app.storage.models import AgentRunRecord, Checkpoint, Conversation, Environment, EvalResult, Message, Project
from app.targets.base import CommandExecutor, TargetError
from app.targets.container import LocalExecutor, LocalFiles
from app.targets.service import RunTarget, describe_target
from app.tracing.snapshots import SnapshotStore, trace_dir

logger = logging.getLogger(__name__)

# A play without a time budget still ends: the agent loop has its own limits, this is the last guard.
MAX_PLAY_SECONDS = 2 * 3600


class PlayError(Exception):
    """The play could not start; the message is safe to show and is stored as the result's detail."""


@dataclass(frozen=True)
class PlayRequest:
    result_id: int
    run_id: int
    project_id: int
    case: EvalCase
    repeat: int
    variant: Variant
    # The variant's Blueprint layers, read at their ref once per run; None is the project's normal layers.
    layers: list[Layer] | None = None


@dataclass
class Started:
    job: ReplyJob
    conversation_id: int
    workspace: Path
    executor: CommandExecutor
    vendor: str
    preset: str | None


def owner(result_id: int) -> str:
    return f"eval-result:{result_id}"


class CasePlayer:
    def __init__(self, services: ReplyServices, work: BackgroundWork[None]) -> None:
        self.services = services
        self.snapshots = SnapshotStore(trace_dir(services.data_dir))
        # The run's work: a stop that a budget asks for runs there, so stopping the run stops it too.
        self.work = work
        self.stopping: set[asyncio.Task[None]] = set()

    async def stop_reply(self, conversation_id: int) -> None:
        await self.services.replies.stop(conversation_id)

    async def play(self, request: PlayRequest) -> None:
        """Play the case and store its result; never raises for the case's own failures."""
        conversation: list[int] = []

        def stop(_reason: str) -> None:
            if conversation:
                task = self.work.start(self.stop_reply(conversation[0]))
                self.stopping.add(task)
                task.add_done_callback(self.stopping.discard)

        pin_owner = owner(request.result_id)
        watch = BudgetWatch(request.case.budget, stop, lambda digest: self.snapshots.pin(digest, pin_owner))
        starting = asyncio.ensure_future(anyio.to_thread.run_sync(self.start, request, watch, conversation))
        try:
            # Shielded: a stop must not leave the thread to launch a reply after the play has given up on it.
            started = await asyncio.shield(starting)
        except PlayError as error:
            await anyio.to_thread.run_sync(self.store_error, request, str(error))
            return
        except asyncio.CancelledError:
            # Stopped while starting: once the start is done, a reply it launched is stopped too. Shielded: the
            # run's scope keeps cancelling every await until the run has ended.
            with anyio.CancelScope(shield=True):
                await asyncio.wait({starting})
                if not starting.cancelled() and starting.exception() is not None:
                    conversation.clear()  # It failed before or while launching: nothing to stop.
                if conversation:
                    await self.services.replies.stop(conversation[0])
                await anyio.to_thread.run_sync(self.store_error, request, "Stopped.", "stopped")
            raise
        except Exception:  # A background play: the run goes on with its other cases, the failure is logged.
            logger.exception("Eval play %s could not start.", request.result_id)
            await anyio.to_thread.run_sync(self.store_error, request, "The play could not start; see the log.")
            return
        try:
            await self.wait(started, request.case, watch)
            result, used = await self.judge(request, started, watch)
            await anyio.to_thread.run_sync(self.store, request, started, result, used, watch.overrun)
        except asyncio.CancelledError:
            # The run was stopped: the reply stops too, and the play is kept as stopped with its workspace.
            with anyio.CancelScope(shield=True):
                await self.services.replies.stop(started.conversation_id)
                await anyio.to_thread.run_sync(self.store_error, request, "Stopped.", "stopped")
            raise
        except Exception:  # A background play: the run goes on with its other cases, the failure is logged.
            logger.exception("Eval play %s failed.", request.result_id)
            await anyio.to_thread.run_sync(self.store_error, request, "The play failed; see the log.")
        finally:
            for digest in watch.snapshots:
                self.snapshots.unpin(digest, owner(request.result_id))

    # --- Starting -------------------------------------------------------------------------------------------------

    def start(self, request: PlayRequest, watch: BudgetWatch, conversation_id: list[int]) -> Started:
        """In a worker thread: the workspace, the conversation and the reply."""
        services, case = self.services, request.case
        with services.session_factory() as session:
            project = session.get_one(Project, request.project_id)
            environment = session.get_one(Environment, project.environment_id)
            folder = run_folder(services.workspace_dir, environment.slug, request.run_id) / play_name(
                case.name, request.repeat
            )
            result = session.get_one(EvalResult, request.result_id)
            result.status, result.started_at = "running", datetime.now(UTC)
            result.workspace = folder.relative_to(services.workspace_dir).as_posix()
            session.commit()
            try:
                make_workspace(
                    project_path(services.workspace_dir, project),
                    folder,
                    case.start,
                    services.data_dir,
                    project.id,
                    lambda number: checkpoint_tree(session, project.id, number),
                    environment.id,
                )
            except WorkspaceError as error:
                raise PlayError(str(error)) from error
            conversation = play_conversation(session, services, project, request)
            conversation_id.append(conversation.id)
            result.conversation_id, result.model = conversation.id, conversation.model
            session.commit()
            service = ReplyService(services)
            with services.replies.reserve(conversation.id) as reservation:
                conversation = session.get(Conversation, conversation.id, populate_existing=True)
                if conversation is None:
                    raise PlayError("The eval conversation no longer exists.")
                try:
                    setup = service.prepare(session, conversation, case.prompt)
                    setup, target = play_setup(session, services, conversation, setup, folder, request.layers)
                except (ReplyError, TargetError) as error:
                    raise PlayError(str(error)) from error
                user_message = add_user_message(session, conversation, case.prompt)
                reply = ReplyRequest(
                    conversation, user_message, unattended=True, tools_by_name=case.allowed_tools or (), on_step=watch
                )
                watch.started = time.monotonic()
                try:
                    job = service.launch(session, setup, reply, reservation)
                except ReplyError as error:
                    raise PlayError(str(error)) from error
            executor: CommandExecutor = (
                target.runtime.executor(str(folder)) if target is not None else LocalExecutor(str(folder))
            )
            return Started(job, conversation.id, folder, executor, setup.provider_spec.name, conversation.preset)

    async def wait(self, started: Started, case: EvalCase, watch: BudgetWatch) -> None:
        """Until the reply ends; a time budget that runs out stops it, as an overrun of any other limit does."""
        task = started.job.task
        if task is None:
            return
        limit = case.budget.seconds if case.budget is not None and case.budget.seconds is not None else None
        # asyncio.wait, not wait_for: the reply's task ends cancelled when the budget stops it, which is no reason to
        # cancel the play itself.
        done, _ = await asyncio.wait({task}, timeout=min(limit or MAX_PLAY_SECONDS, MAX_PLAY_SECONDS))
        if not done:
            watch.check()
            if watch.overrun is None:
                watch.overrun = "the play ran out of time"
            await self.services.replies.stop(started.conversation_id)
        if self.stopping:
            # The budget's stop of the reply has stored what arrived; wait for it before the reply is read.
            await asyncio.wait(set(self.stopping), timeout=30)

    # --- Judging --------------------------------------------------------------------------------------------------

    async def judge(self, request: PlayRequest, started: Started, watch: BudgetWatch) -> tuple[CaseResult, PlayUsage]:
        seconds = watch.usage().seconds
        reply, used = await anyio.to_thread.run_sync(self.reply_and_usage, started.conversation_id, seconds)
        scorer = ModelRubricScorer(
            context=self.services.utility,
            environment_id=self.environment_id(request.project_id),
            project_id=request.project_id,
            preset=started.preset,
            vendor_under_test=started.vendor,
        )
        context = CheckContext(LocalFiles(str(started.workspace)), started.executor, reply, used.usage, scorer)
        return await evaluate_case(request.case, context), used

    def environment_id(self, project_id: int) -> int:
        with self.services.session_factory() as session:
            return session.get_one(Project, project_id).environment_id

    def reply_and_usage(self, conversation_id: int, seconds: float) -> tuple[str, PlayUsage]:
        with self.services.session_factory() as session:
            conversation = session.get_one(Conversation, conversation_id)
            head = session.get(Message, conversation.head_message_id) if conversation.head_message_id else None
            reply = message_text(head) if head is not None and head.role == "assistant" else ""
            return reply, play_usage(session, conversation_id, seconds)

    # --- Storing --------------------------------------------------------------------------------------------------

    def store(
        self, request: PlayRequest, started: Started, result: CaseResult, used: PlayUsage, overrun: str | None
    ) -> None:
        checks = list(result.checks)
        status = result.status
        detail = None
        if overrun is not None:
            # Stopped during the run: the budget check fails whatever the usage stored afterwards says.
            detail = f"Stopped during the run: over budget ({overrun})."
            checks = [check for check in checks if check.kind != "budget"]
            checks.append(CheckResult("budget", "budget", "failed", f"Over budget: {overrun}."))
            status = "failed"
        with self.services.session_factory() as session:
            row = session.get_one(EvalResult, request.result_id)
            usage = used.usage
            row.status, row.detail, row.checks = status, detail, [check_json(check) for check in checks]
            row.steps, row.tokens, row.cost = usage.steps, usage.tokens, usage.cost_usd
            row.seconds = round(usage.seconds, 1)
            row.asked, row.scorer_cost = used.asked, CaseResult(result.case, status, tuple(checks)).scorer_cost_usd
            row.agent_run_id = root_run(session, started.conversation_id)
            row.finished_at = datetime.now(UTC)
            self.settle_workspace(row, status == "passed")
            session.commit()

    def store_error(self, request: PlayRequest, detail: str, status: str = "error") -> None:
        with self.services.session_factory() as session:
            row = session.get_one(EvalResult, request.result_id)
            row.status, row.detail, row.finished_at = status, detail, datetime.now(UTC)
            self.settle_workspace(row, False)
            session.commit()

    def settle_workspace(self, row: EvalResult, passed: bool) -> None:
        """A passed play's workspace goes now; a failed one stays for a while to look at."""
        if row.workspace is None:
            return
        if passed or not (self.services.workspace_dir / row.workspace).exists():
            try:
                remove_workspace(self.services.workspace_dir, row.workspace)
            except (WorkspaceError, OSError):
                logger.warning("The eval workspace %s could not be removed now.", row.workspace, exc_info=True)
                row.workspace_expires_at = datetime.now(UTC)
                return
            row.workspace, row.workspace_expires_at = None, None
        else:
            row.workspace_expires_at = datetime.now(UTC) + KEEP_FAILED


def check_json(check: CheckResult) -> dict[str, object]:
    cost = check.scorer_cost
    return {
        "kind": check.kind,
        "name": check.name,
        "status": check.status,
        "detail": check.detail,
        "scorer_cost": cost.cost_usd if cost is not None else None,
        "scorer_model": cost.model if cost is not None else None,
    }


def checkpoint_tree(session: Session, project_id: int, number: int) -> str:
    checkpoint = session.get(Checkpoint, number)
    if checkpoint is None or checkpoint.project_id != project_id:
        raise WorkspaceError(f"The project has no checkpoint {number}; it may have been pruned.")
    return checkpoint.tree


def root_run(session: Session, conversation_id: int) -> int | None:
    return session.scalar(
        select(AgentRunRecord.id)
        .where(AgentRunRecord.conversation_id == conversation_id, AgentRunRecord.parent_run_id.is_(None))
        .order_by(AgentRunRecord.id)
    )


def play_conversation(
    session: Session, services: ReplyServices, project: Project, request: PlayRequest
) -> Conversation:
    """An archived conversation in the project for the play: out of the sidebar, linked from the Evals tab."""
    case, variant = request.case, request.variant
    name = variant.preset or case.preset or project.default_preset
    preset = find_preset_by_name(session, project.environment_id, name) if name else None
    if name and preset is None and (variant.preset or case.preset):
        raise PlayError(f"The preset {name} does not exist in this environment.")
    preset = preset or default_preset(session, project.environment_id)
    if preset is None:
        raise PlayError("The environment has no model preset to work with.")
    model = services.providers.find_model(preset.model)
    if model is None:
        raise PlayError(f"The model {preset.model} of the preset {preset.name} is not available.")
    now = datetime.now(UTC)
    conversation = Conversation(
        environment_id=project.environment_id,
        project_id=project.id,
        agent=variant.agent or case.agent,
        pinned_skills=[],
        preset=preset.name,
        title=f"Eval {request.run_id} · {play_name(case.name, request.repeat)}"[:200],
        provider=model.provider,
        model=model.id,
        effort=preset.effort if preset.effort in model.efforts else model.efforts[0],
        access="default",
        created_at=now,
        updated_at=now,
        archived_at=now,
    )
    session.add(conversation)
    session.flush()
    return conversation


def play_setup(
    session: Session,
    services: ReplyServices,
    conversation: Conversation,
    setup: ReplySetup,
    workspace: Path,
    layers: list[Layer] | None,
) -> tuple[ReplySetup, RunTarget | None]:
    """The reply's setup for the workspace: its folder, where its commands run, and the variant's layers."""
    layers = layers if layers is not None else setup.layers
    agent = find_agent(layers, conversation.agent)
    if conversation.agent and agent is None:
        raise PlayError(f"The agent {conversation.agent} does not exist in this project's Blueprint.")
    target = workspace_target(session, services, conversation, workspace) if setup.agent_project is not None else None
    agent_text = agent_part(agent, layers) if agent else None
    system = build_system_parts(layers, setup.project_text, agent_text, setup.languages)
    if target is not None:
        system.append(SystemPart(text=describe_target(target.target, target.root), source="target"))
    return replace(setup, layers=layers, agent=agent, system=system, target=target, workspace=workspace), target


def workspace_target(
    session: Session, services: ReplyServices, conversation: Conversation, workspace: Path
) -> RunTarget | None:
    """The environment's runner, with the workspace as the folder its commands start in; None when commands run in
    YAAP's own process (YAAP_RUNNER=off). A project's own sandbox or computer cannot reach the workspace, so a play
    always uses the runner."""
    if not RUNNERS.enabled or RUNNERS.workspace_dir is None:
        return None
    target, runtime = RUNNERS.target(
        session, conversation.environment_id, f"Eval case in project {conversation.project_id}", conversation.id
    )
    return RunTarget(target, runtime, runner_path(runtime.environment_dir, str(workspace)))
