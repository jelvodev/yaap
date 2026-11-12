"""Replaying a run from one of its model calls.

A replay starts a new reply in an archived conversation of the project (linked from the original, as an eval play is
from its run) with the messages the replayed call saw (history.py), in a throwaway workspace with the files of that
moment (files.py, made by the eval workspace), in a variant of the original's setup: another preset, model or one
Blueprint layer at a git ref (the eval variant). It runs unattended as an eval play does: nobody is there to approve,
so a call that would ask is refused and counted as *asked*. The original run, its conversation and the project folder
are only read. While the replay runs, its snapshot and checkpoint are pinned; the workspace goes when it ends.
"""

import asyncio
import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import anyio
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.blueprint.layers import Layer
from app.blueprint.levels import layers_for
from app.checkpoints.pins import pin_checkpoint, replay_owner, unpin_checkpoint
from app.checkpoints.service import CheckpointStore
from app.core.background import BackgroundWork
from app.core.chat import add_user_message
from app.core.replies import ReplyJob
from app.core.reply_errors import ReplyError
from app.core.reply_service import ReplyRequest, ReplyService
from app.core.reply_services import ReplyServices
from app.evals.models import StartState
from app.evals.play import MAX_PLAY_SECONDS, PlayError, checkpoint_tree, play_setup, root_run
from app.evals.variants import Variant, VariantError, stored_variant, variant_layers
from app.evals.workspace import WorkspaceError, make_workspace, remove_workspace, runs_folder
from app.presets.service import find_preset_by_name
from app.projects.service import ProjectError, project_path
from app.replays.files import FilesError, files_checkpoint
from app.replays.history import SnapshotMessagesError, last_user_text, model_history, snapshot_messages
from app.replays.summary import asked_count
from app.storage.models import AgentRunRecord, Conversation, Environment, Project, Replay
from app.targets.base import TargetError
from app.tracing.snapshots import SnapshotStore, trace_dir
from app.tracing.steps import find_step

logger = logging.getLogger(__name__)

REPLAYS_FOLDER = "replays"
OPEN = ("queued", "running")


class ReplayError(Exception):
    """A replay that cannot be started or used; the message is safe to show."""


class ReplayNotFound(ReplayError):
    """The step or replay is not in this conversation."""


@dataclass(frozen=True)
class ReplayStart:
    conversation_id: int
    step_id: int
    variant: Variant


@dataclass(frozen=True)
class Started:
    job: ReplyJob
    conversation_id: int


def workspace_folder(workspace_dir: Path, environment_slug: str, replay_id: int) -> Path:
    """Beside the eval runs' workspaces, so the environment's runner reaches it and the eval cleanup applies."""
    return runs_folder(workspace_dir, environment_slug) / REPLAYS_FOLDER / str(replay_id)


def replay_data_dir(data_dir: Path, replay_id: int) -> Path:
    return data_dir / REPLAYS_FOLDER / str(replay_id)


class ReplayService:
    def __init__(self, services: Callable[[], ReplyServices]) -> None:
        # Read when a replay starts, so a test that changes a switch on the application's state sees its change.
        self.services_factory = services
        self.tasks: dict[int, asyncio.Task[None]] = {}
        # Shutdown stops this before the replies, so a replay does not go on to store once its reply has stopped.
        self.work: BackgroundWork[None] = BackgroundWork()

    def services(self) -> ReplyServices:
        """The reply services of a replay: no titles, memory suggestions or summaries for its conversation."""
        return replace(
            self.services_factory(),
            utility_titles=False,
            memory_suggestions=False,
            memory_tidy=False,
            background_summaries=False,
        )

    # --- Starting (in a worker thread) ----------------------------------------------------------------------------

    def start(self, start: ReplayStart) -> int:
        """Check the step, find its files, store the replay and start it in the background."""
        services = self.services_factory()
        with services.session_factory() as session:
            replay_id = self.create(session, services, start)
        anyio.from_thread.run_sync(self.launch, replay_id)
        return replay_id

    def create(self, session: Session, services: ReplyServices, start: ReplayStart) -> int:
        located = find_step(session, start.step_id)
        if located is None or located.conversation_id != start.conversation_id:
            raise ReplayNotFound("Step not found in this conversation.")
        step = located.step
        if step.kind != "model":
            raise ReplayError("Only a model call can be replayed.")
        record = session.get_one(AgentRunRecord, step.run_id)
        if record.parent_run_id is not None:
            raise ReplayError("A model call of a delegated sub-run cannot be replayed; replay one of the reply's own.")
        if record.status == "running":
            raise ReplayError("The reply is still running; replay it once it is done.")
        snapshots = SnapshotStore(trace_dir(services.data_dir))
        stored = snapshots.load(step.snapshot) if step.snapshot else None
        if step.snapshot is None or stored is None:
            raise ReplayError("What this call saw is not kept (snapshots go after 30 days), so it cannot be replayed.")
        try:
            snapshot_messages(stored)
        except SnapshotMessagesError as error:
            raise ReplayError(str(error)) from error
        source = session.get_one(Conversation, start.conversation_id)
        if source.project_id is None:
            raise ReplayError("Only a reply in a project can be replayed: a replay works in a copy of its files.")
        project = session.get_one(Project, source.project_id)
        check_variant(session, services, source, start.variant)
        try:
            folder = project_path(services.workspace_dir, project)
            store = CheckpointStore(services.session_factory, services.data_dir)
            checkpoint_id = files_checkpoint(session, store, project.id, folder, step.started_at, step.position)
        except (ProjectError, FilesError) as error:
            raise ReplayError(str(error)) from error
        replay = Replay(
            project_id=project.id,
            environment_id=project.environment_id,
            source_conversation_id=source.id,
            source_run_id=record.id,
            source_step_id=step.id,
            step_position=step.position,
            snapshot=step.snapshot,
            checkpoint_id=checkpoint_id,
            variant=start.variant.as_json(),
            status="queued",
            asked=0,
            created_at=datetime.now(UTC),
        )
        session.add(replay)
        session.flush()
        pin_checkpoint(session, checkpoint_id, replay_owner(replay.id))
        session.commit()
        snapshots.pin(step.snapshot, replay_owner(replay.id))
        return replay.id

    def launch(self, replay_id: int) -> None:
        """On the event loop."""
        task = self.work.start(self.execute(replay_id), name=f"replay-{replay_id}")
        self.tasks[replay_id] = task
        task.add_done_callback(lambda _task: self.tasks.pop(replay_id, None))

    # --- Running (on the event loop) ------------------------------------------------------------------------------

    async def execute(self, replay_id: int) -> None:
        services = self.services()
        conversation: list[int] = []
        starting = asyncio.ensure_future(anyio.to_thread.run_sync(self.begin, services, replay_id, conversation))
        try:
            # Shielded: a stop must not leave the thread to launch a reply after the replay has given up on it.
            started = await asyncio.shield(starting)
            task = started.job.task
            if task is not None:
                done, _ = await asyncio.wait({task}, timeout=MAX_PLAY_SECONDS)
                if not done:
                    await services.replies.stop(started.conversation_id)
            await anyio.to_thread.run_sync(self.finish, services, replay_id, "done", None)
        except ReplayError as error:
            await anyio.to_thread.run_sync(self.finish, services, replay_id, "error", str(error))
        except asyncio.CancelledError:
            # Shielded: the replay's scope keeps cancelling every await until the replay has ended.
            with anyio.CancelScope(shield=True):
                if not starting.done():
                    await asyncio.wait({starting})
                if conversation:
                    await services.replies.stop(conversation[0])
                await anyio.to_thread.run_sync(self.finish, services, replay_id, "stopped", "Stopped.")
            raise
        except Exception:  # A background task: the replay ends as error and the cause is logged.
            logger.exception("Replay %s failed.", replay_id)
            await anyio.to_thread.run_sync(self.finish, services, replay_id, "error", "The replay failed; see the log.")
        finally:
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(self.clean_up, services, replay_id)

    def begin(self, services: ReplyServices, replay_id: int, conversation_ids: list[int]) -> Started:
        """In a worker thread: the workspace, the conversation and the reply."""
        with services.session_factory() as session:
            replay = session.get_one(Replay, replay_id)
            replay.status, replay.started_at = "running", datetime.now(UTC)
            session.commit()
            project = session.get_one(Project, replay.project_id)
            environment = session.get_one(Environment, project.environment_id)
            source = session.get_one(Conversation, replay.source_conversation_id)
            variant = stored_variant(replay.variant)
            stored = SnapshotStore(trace_dir(services.data_dir)).load(replay.snapshot)
            if stored is None or replay.checkpoint_id is None:
                raise ReplayError("What the call saw, or the checkpoint of its files, is gone.")
            try:
                messages = snapshot_messages(stored)
            except SnapshotMessagesError as error:
                raise ReplayError(str(error)) from error
            folder = workspace_folder(services.workspace_dir, environment.slug, replay_id)
            try:
                make_workspace(
                    project_path(services.workspace_dir, project),
                    folder,
                    StartState(checkpoint=replay.checkpoint_id),
                    services.data_dir,
                    project.id,
                    lambda number: checkpoint_tree(session, project.id, number),
                    environment.id,
                )
                layers = self.layers(services, replay_id, environment, project, variant)
            except (WorkspaceError, VariantError, ProjectError) as error:
                raise ReplayError(str(error)) from error
            conversation = replay_conversation(session, services, source, replay, variant)
            conversation_ids.append(conversation.id)
            replay.conversation_id = conversation.id
            session.commit()
            prompt = last_user_text(messages)
            service = ReplyService(services)
            with services.replies.reserve(conversation.id) as reservation:
                conversation = session.get(Conversation, conversation.id, populate_existing=True)
                if conversation is None:
                    raise ReplayError("The replay conversation no longer exists.")
                try:
                    setup = service.prepare(session, conversation, prompt)
                    setup, _target = play_setup(session, services, conversation, setup, folder, layers)
                    text = prompt or f"Replay of step {replay.step_position}"
                    user_message = add_user_message(session, conversation, text)
                    history = tuple(model_history(messages))
                    request = ReplyRequest(conversation, user_message, unattended=True, history=history)
                    job = service.launch(session, setup, request, reservation)
                except (ReplyError, TargetError, PlayError) as error:
                    raise ReplayError(str(error)) from error
            replay.agent_run_id = root_run(session, conversation.id)
            session.commit()
            return Started(job, conversation.id)

    def layers(
        self, services: ReplyServices, replay_id: int, environment: Environment, project: Project, variant: Variant
    ) -> list[Layer] | None:
        if variant.layer is None:
            return None
        layers = layers_for(services.blueprint_dir, services.workspace_dir, environment, project)
        return variant_layers(layers, variant, replay_data_dir(services.data_dir, replay_id) / "layers")

    def finish(self, services: ReplyServices, replay_id: int, status: str, detail: str | None) -> None:
        with services.session_factory() as session:
            replay = session.get(Replay, replay_id)
            if replay is None:
                return
            replay.status, replay.detail, replay.finished_at = status, detail, datetime.now(UTC)
            if replay.conversation_id is not None:
                replay.agent_run_id = root_run(session, replay.conversation_id)
                replay.asked = asked_count(session, replay.conversation_id)
            session.commit()

    def clean_up(self, services: ReplyServices, replay_id: int) -> None:
        """The workspace and the copy of a variant's layer go; the pins of the run are released."""
        with services.session_factory() as session:
            replay = session.get(Replay, replay_id)
            if replay is not None:
                release(session, services.data_dir, replay)
                environment = session.get_one(Environment, replay.environment_id)
                remove_replay_workspace(services.workspace_dir, environment.slug, replay_id)
                session.commit()
        shutil.rmtree(replay_data_dir(services.data_dir, replay_id), ignore_errors=True)

    # --- Stopping -------------------------------------------------------------------------------------------------

    async def stop(self, replay_id: int) -> bool:
        task = self.tasks.get(replay_id)
        if task is None:
            return False
        # Through its scope, so this waits for a worker thread the replay is in; the scope ends the cancellation.
        self.work.cancel(task)
        await self.work.wait(task)
        return True


def check_variant(session: Session, services: ReplyServices, source: Conversation, variant: Variant) -> None:
    """Refuse a preset or model that does not exist before anything is stored."""
    if variant.preset and find_preset_by_name(session, source.environment_id, variant.preset) is None:
        raise ReplayError(f"The preset {variant.preset} does not exist in this environment.")
    if variant.model and services.providers.find_model(variant.model) is None:
        raise ReplayError(f"The model {variant.model} is not available.")


def replay_conversation(
    session: Session, services: ReplyServices, source: Conversation, replay: Replay, variant: Variant
) -> Conversation:
    """An archived conversation in the project for the replay, with the original's agent and the variant's preset and
    model (the original's where the variant names none)."""
    preset = find_preset_by_name(session, source.environment_id, variant.preset) if variant.preset else None
    if variant.preset and preset is None:
        raise ReplayError(f"The preset {variant.preset} does not exist in this environment.")
    model_id = variant.model or (preset.model if preset is not None else source.model)
    model = services.providers.find_model(model_id)
    if model is None:
        raise ReplayError(f"The model {model_id} is not available.")
    effort = preset.effort if preset is not None else source.effort
    now = datetime.now(UTC)
    conversation = Conversation(
        environment_id=source.environment_id,
        project_id=source.project_id,
        agent=source.agent,
        pinned_skills=[],
        preset=preset.name if preset is not None else source.preset,
        title=f"Replay {replay.id} · step {replay.step_position} of {source.title or 'a conversation'}"[:200],
        provider=model.provider,
        model=model.id,
        effort=effort if effort in model.efforts else model.efforts[0],
        access="default",
        created_at=now,
        updated_at=now,
        archived_at=now,
    )
    session.add(conversation)
    session.flush()
    return conversation


def release(session: Session, data_dir: Path, replay: Replay) -> None:
    """The run's own pins; an eval case made from the replay keeps its pins. The caller commits."""
    SnapshotStore(trace_dir(data_dir)).unpin(replay.snapshot, replay_owner(replay.id))
    if replay.checkpoint_id is not None:
        unpin_checkpoint(session, replay.checkpoint_id, replay_owner(replay.id))


def remove_replay_workspace(workspace_dir: Path, environment_slug: str, replay_id: int) -> None:
    folder = workspace_folder(workspace_dir, environment_slug, replay_id)
    if not folder.exists():
        return
    try:
        remove_workspace(workspace_dir, folder.relative_to(workspace_dir).as_posix())
    except (WorkspaceError, OSError):
        logger.warning("The replay workspace %s could not be removed.", folder, exc_info=True)


def recover_replays(session: Session, workspace_dir: Path, data_dir: Path) -> int:
    """Replays that were going on when YAAP stopped cannot go on: they end as stopped, their workspaces and pins go."""
    now = datetime.now(UTC)
    replays = list(session.scalars(select(Replay).where(Replay.status.in_(OPEN))))
    for replay in replays:
        replay.status, replay.detail, replay.finished_at = "stopped", "YAAP restarted during the replay.", now
        release(session, data_dir, replay)
        environment = session.get_one(Environment, replay.environment_id)
        remove_replay_workspace(workspace_dir, environment.slug, replay.id)
        shutil.rmtree(replay_data_dir(data_dir, replay.id), ignore_errors=True)
    session.commit()
    return len(replays)
