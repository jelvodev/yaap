"""Eval runs: starting them, playing their cases in the background, stopping, removing.

A run is started from the project panel's Evals tab, by `/eval` in a project conversation, or by a scheduled task
with the action *eval*; all three call `EvalService.start`. Its results are made at once, one per case and repeat, as
*queued*, so its progress is readable while it runs. The plays run as background tasks on YAAP's event loop: at most
`eval_parallel_cases` of them per environment at a time, over all runs (one by default). A run of a variant reads
its Blueprint layer at the variant's ref once, into YAAP's data folder, and removes it at the end.
"""

import asyncio
import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import anyio
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.blueprint.layers import Layer
from app.blueprint.levels import layers_for
from app.core.background import BackgroundWork
from app.core.conversations import delete_conversation
from app.core.reply_errors import CommandRefused
from app.core.reply_services import ReplyServices
from app.core.settings import SETTING_DEFINITIONS, resolve_setting
from app.evals.cases import read_cases
from app.evals.compare import Play
from app.evals.models import EvalCase
from app.evals.play import CasePlayer, PlayRequest
from app.evals.variants import Variant, VariantError, stored_variant, variant_layers
from app.evals.workspace import KEEP_FAILED, WorkspaceError, remove_workspace
from app.projects.service import project_path
from app.storage.models import Conversation, Environment, EvalResult, EvalRun, Project

logger = logging.getLogger(__name__)

MAX_REPEATS = 10
COMPARISON_REPEATS = 3
OPEN_RUN = ("queued", "running")
OPEN_PLAY = ("queued", "running")


class EvalError(Exception):
    """An eval run that cannot be started or changed; the message is safe to show."""


@dataclass(frozen=True)
class RunStart:
    project_id: int
    trigger: str
    case_name: str | None = None
    repeats: int = 1
    variant: Variant = Variant()
    baseline_run_id: int | None = None
    scheduled_run_id: int | None = None


def project_cases(workspace_dir: Path, project: Project) -> dict[str, EvalCase]:
    """The project's valid cases by name."""
    return {file.name: file.case for file in read_cases(project_path(workspace_dir, project)) if file.case is not None}


def run_summary(statuses: list[str]) -> str:
    passed = statuses.count("passed")
    errors = statuses.count("error") + statuses.count("stopped")
    text = f"{passed} of {len(statuses)} passed"
    return f"{text}, {errors} could not be judged" if errors else text


def result_plays(results: list[EvalResult]) -> list[Play]:
    """A run's results as plays for a comparison (evals.compare)."""
    return [
        Play(row.case_name, row.status, row.cost, row.tokens, row.steps)  # type: ignore[arg-type]  # a PlayStatus
        for row in results
    ]


def evals_data_dir(data_dir: Path, run_id: int) -> Path:
    return data_dir / "evals" / str(run_id)


class EvalService:
    def __init__(self, services: Callable[[], ReplyServices]) -> None:
        # Read when a run starts, so a test that changes a switch on the application's state sees its change.
        self.services_factory = services
        self.tasks: dict[int, asyncio.Task[None]] = {}
        # The runs and the stops their plays ask for. Shutdown stops this before the replies, so a play does not go
        # on to judge and store once its reply has stopped.
        self.work: BackgroundWork[None] = BackgroundWork()
        self.busy: dict[int, int] = {}
        self.slots: asyncio.Condition | None = None

    def services(self) -> ReplyServices:
        """The reply services of a play: no titles, memory suggestions or summaries for its conversation."""
        return replace(
            self.services_factory(),
            utility_titles=False,
            memory_suggestions=False,
            memory_tidy=False,
            background_summaries=False,
        )

    # --- Starting (in a worker thread) ----------------------------------------------------------------------------

    def create(self, session: Session, start: RunStart) -> EvalRun:
        """The run and its queued results; refuses an unknown or invalid case, or a project without valid cases."""
        project = session.get(Project, start.project_id)
        if project is None:
            raise EvalError("Project not found.")
        if not 1 <= start.repeats <= MAX_REPEATS:
            raise EvalError(f"Play each case 1 to {MAX_REPEATS} times.")
        cases = project_cases(self.services_factory().workspace_dir, project)
        if start.case_name is not None:
            if start.case_name not in cases:
                raise EvalError(f"The project has no valid eval case {start.case_name}; see the Evals tab.")
            names = [start.case_name]
        else:
            names = sorted(cases)
        if not names:
            raise EvalError("The project has no valid eval cases in .blueprint/evals/.")
        now = datetime.now(UTC)
        run = EvalRun(
            project_id=project.id,
            environment_id=project.environment_id,
            status="queued",
            trigger=start.trigger,
            case_name=start.case_name,
            repeats=start.repeats,
            variant=start.variant.as_json(),
            baseline_run_id=start.baseline_run_id,
            scheduled_run_id=start.scheduled_run_id,
            created_at=now,
        )
        session.add(run)
        session.flush()
        for repeat in range(1, start.repeats + 1):
            for name in names:
                session.add(EvalResult(run_id=run.id, case_name=name, repeat=repeat, status="queued", checks=[]))
        session.commit()
        return run

    def start(self, start: RunStart) -> int:
        """Create the run and start it in the background. Runs in a worker thread of the event loop."""
        with self.services_factory().session_factory() as session:
            run_id = self.create(session, start).id
        anyio.from_thread.run_sync(self.launch, run_id)
        return run_id

    def start_comparison(self, project_id: int, case_name: str | None, variant: Variant, repeats: int) -> list[int]:
        """A comparison: the normal setup and the variant, each case played `repeats` times."""
        if variant.empty:
            raise EvalError("A comparison needs a variant: another preset, agent or Blueprint ref.")
        baseline = self.start(RunStart(project_id, "panel", case_name, repeats))
        return [baseline, self.start(RunStart(project_id, "panel", case_name, repeats, variant, baseline))]

    def start_from_chat(self, conversation: Conversation, case_name: str | None) -> tuple[int, str]:
        """/eval [case] in a project conversation."""
        if conversation.project_id is None:
            raise CommandRefused("/eval runs the eval cases of a project; use it in a project conversation.")
        try:
            run_id = self.start(RunStart(conversation.project_id, "command", case_name))
        except EvalError as error:
            raise CommandRefused(str(error)) from error
        what = f"case {case_name}" if case_name else "every eval case"
        return run_id, f"Eval run {run_id} started for {what}; follow it in the project's Evals tab."

    def launch(self, run_id: int) -> None:
        """On the event loop."""
        task = self.work.start(self.execute(run_id), name=f"eval-run-{run_id}")
        self.tasks[run_id] = task
        task.add_done_callback(lambda _task: self.tasks.pop(run_id, None))

    # --- Running (on the event loop) ------------------------------------------------------------------------------

    async def execute(self, run_id: int) -> None:
        services = self.services()
        try:
            requests, environment_id, limit = await anyio.to_thread.run_sync(self.prepare, services, run_id)
            player = CasePlayer(services, self.work)
            queue = list(requests)

            async def worker() -> None:
                while queue:
                    request = queue.pop(0)
                    await self.acquire(environment_id, limit)
                    try:
                        await player.play(request)
                    finally:
                        with anyio.CancelScope(shield=True):
                            await self.release(environment_id)

            # A task group rather than gather: a stop reaches the plays through the run's cancel scope, which leaves
            # their shielded cleanup alone; gather would cancel them again each time the run is cancelled.
            async with anyio.create_task_group() as group:
                for _ in range(min(limit, len(requests)) or 1):
                    group.start_soon(worker)
            await anyio.to_thread.run_sync(self.finish, services, run_id, "done", None)
        except asyncio.CancelledError:
            # Shielded: the run's scope keeps cancelling every await until the run has ended.
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(self.finish, services, run_id, "stopped", "Stopped.")
            raise
        except (VariantError, EvalError) as error:
            await anyio.to_thread.run_sync(self.finish, services, run_id, "error", str(error))
        except Exception:  # A background task: the run ends as error and the cause is logged.
            logger.exception("Eval run %s failed.", run_id)
            await anyio.to_thread.run_sync(self.finish, services, run_id, "error", "The run failed; see the log.")
        finally:
            shutil.rmtree(evals_data_dir(services.data_dir, run_id), ignore_errors=True)

    def prepare(self, services: ReplyServices, run_id: int) -> tuple[list[PlayRequest], int, int]:
        """The plays of the run with their cases (read now), and how many may play at once in the environment."""
        with services.session_factory() as session:
            run = session.get_one(EvalRun, run_id)
            run.status = "running"
            project = session.get_one(Project, run.project_id)
            environment = session.get_one(Environment, project.environment_id)
            variant = stored_variant(run.variant)
            layers = self.layers(services, session, run_id, environment, project, variant)
            cases = project_cases(services.workspace_dir, project)
            requests = []
            for result in session.scalars(
                select(EvalResult).where(EvalResult.run_id == run_id).order_by(EvalResult.repeat, EvalResult.id)
            ):
                case = cases.get(result.case_name)
                if case is None:
                    result.status, result.detail = "error", "The case is gone or no longer valid."
                    result.finished_at = datetime.now(UTC)
                    continue
                requests.append(PlayRequest(result.id, run_id, project.id, case, result.repeat, variant, layers))
            setting = resolve_setting(session, SETTING_DEFINITIONS["eval_parallel_cases"], environment.id).value
            session.commit()
            return requests, environment.id, max(int(setting), 1)

    def layers(
        self,
        services: ReplyServices,
        session: Session,
        run_id: int,
        environment: Environment,
        project: Project,
        variant: Variant,
    ) -> list[Layer] | None:
        if variant.layer is None:
            return None
        layers = layers_for(services.blueprint_dir, services.workspace_dir, environment, project)
        return variant_layers(layers, variant, evals_data_dir(services.data_dir, run_id) / "layers")

    async def acquire(self, environment_id: int, limit: int) -> None:
        if self.slots is None:
            self.slots = asyncio.Condition()
        async with self.slots:
            await self.slots.wait_for(lambda: self.busy.get(environment_id, 0) < limit)
            self.busy[environment_id] = self.busy.get(environment_id, 0) + 1

    async def release(self, environment_id: int) -> None:
        if self.slots is None:
            return
        async with self.slots:
            self.busy[environment_id] = max(self.busy.get(environment_id, 0) - 1, 0)
            self.slots.notify_all()

    def finish(self, services: ReplyServices, run_id: int, status: str, problem: str | None) -> None:
        with services.session_factory() as session:
            run = session.get(EvalRun, run_id)
            if run is None:
                return
            now = datetime.now(UTC)
            results = list(session.scalars(select(EvalResult).where(EvalResult.run_id == run_id)))
            for result in results:
                if result.status in OPEN_PLAY:
                    result.status, result.detail, result.finished_at = "stopped", problem or "Not played.", now
            run.status, run.finished_at = status, now
            summary = run_summary([result.status for result in results])
            run.summary = f"{problem} {summary}." if problem else f"{summary}."
            session.commit()

    # --- Stopping and removing ------------------------------------------------------------------------------------

    def running(self, run_id: int) -> bool:
        return run_id in self.tasks

    async def stop(self, run_id: int) -> bool:
        """Stop a run: its playing case's reply stops, the cases not played yet are not played."""
        task = self.tasks.get(run_id)
        if task is None:
            return False
        # Through its scope, so this waits for a worker thread the run is in; the scope ends the cancellation.
        self.work.cancel(task)
        await self.work.wait(task)
        return True


def delete_run(session: Session, files_dir: Path, workspace_dir: Path, run: EvalRun) -> None:
    """A finished run, its results, the conversations of its plays and their kept workspaces."""
    if run.status in OPEN_RUN:
        raise EvalError("This run is still going on; stop it first.")
    results = list(session.scalars(select(EvalResult).where(EvalResult.run_id == run.id)))
    for result in results:
        if result.workspace is not None:
            try:
                remove_workspace(workspace_dir, result.workspace)
            except (WorkspaceError, OSError):
                logger.warning("The eval workspace %s could not be removed.", result.workspace, exc_info=True)
    conversations = [result.conversation_id for result in results if result.conversation_id is not None]
    for conversation_id in conversations:
        conversation = session.get(Conversation, conversation_id)
        if conversation is not None:
            delete_conversation(session, files_dir, conversation)
    session.execute(delete(EvalResult).where(EvalResult.run_id == run.id))
    session.execute(update(EvalRun).where(EvalRun.baseline_run_id == run.id).values(baseline_run_id=None))
    session.delete(run)
    session.commit()


def has_other_conversations(session: Session, project_id: int) -> bool:
    """Whether the project has conversations besides those of its eval plays (which removing the project removes)."""
    plays = select(EvalResult.conversation_id).join(EvalRun, EvalRun.id == EvalResult.run_id).where(
        EvalRun.project_id == project_id, EvalResult.conversation_id.is_not(None)
    )
    found = session.scalar(
        select(Conversation.id).where(Conversation.project_id == project_id, Conversation.id.not_in(plays)).limit(1)
    )
    return found is not None


def delete_project_runs(session: Session, files_dir: Path, workspace_dir: Path, project_id: int) -> None:
    """Before a project is removed: its eval runs, with the conversations that would otherwise keep it."""
    runs = list(session.scalars(select(EvalRun).where(EvalRun.project_id == project_id)))
    if any(run.status in OPEN_RUN for run in runs):
        raise EvalError("An eval run of this project is still going on; stop it first.")
    for run in runs:
        delete_run(session, files_dir, workspace_dir, run)


def prune_workspaces(session: Session, workspace_dir: Path, now: datetime | None = None) -> int:
    """Remove the kept workspaces of failed plays whose time is up; returns how many went. Called from the minute
    loop."""
    now = now or datetime.now(UTC)
    expired = list(
        session.scalars(
            select(EvalResult).where(EvalResult.workspace.is_not(None), EvalResult.workspace_expires_at <= now)
        )
    )
    for result in expired:
        try:
            remove_workspace(workspace_dir, result.workspace or "")
        except (WorkspaceError, OSError):
            logger.warning("The eval workspace %s could not be removed.", result.workspace, exc_info=True)
            continue
        result.workspace, result.workspace_expires_at = None, None
    session.commit()
    return len(expired)


def recover_runs(session: Session) -> int:
    """Runs that were going on when YAAP stopped cannot go on: they end as stopped; kept workspaces get their time."""
    now = datetime.now(UTC)
    runs = list(session.scalars(select(EvalRun).where(EvalRun.status.in_(OPEN_RUN))))
    for run in runs:
        results = list(session.scalars(select(EvalResult).where(EvalResult.run_id == run.id)))
        for result in results:
            if result.status in OPEN_PLAY:
                result.status, result.detail, result.finished_at = "stopped", "YAAP restarted during the run.", now
                if result.workspace is not None:
                    result.workspace_expires_at = now + KEEP_FAILED
        run.status, run.finished_at = "stopped", now
        run.summary = f"YAAP restarted during the run. {run_summary([result.status for result in results])}."
    session.commit()
    return len(runs)
