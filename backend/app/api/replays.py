"""Replays: replay a reply from one of its model calls, follow it beside the original, stop it,
and turn it into an eval case. Every route is under the original's conversation, as the trace routes are."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.conversations import ConversationDependency
from app.api.dependencies import DataDirDependency, SessionDependency, WorkspaceDirDependency
from app.evals.case_writer import CaseExistsError, CaseWriteError
from app.evals.variants import VariantError, checked_variant, stored_variant
from app.projects.service import ProjectError
from app.replays.blueprint import replay_layers_from_json
from app.replays.service import ReplayError, ReplayNotFound, ReplayService, ReplayStart
from app.replays.summary import EMPTY, SideSummary, side_summary
from app.replays.to_case import CaseFromReplayError, case_from_replay
from app.storage.models import Conversation, Replay, RunStep

router = APIRouter(tags=["replays"])

ReplayStatus = Literal["queued", "running", "done", "stopped", "error"]
LayerKind = Literal["global", "environment", "project"]


class ReplayIn(BaseModel):
    """What differs from the original; all empty replays it as it was set up."""

    preset: str | None = Field(default=None, max_length=40)
    model: str | None = Field(default=None, max_length=80)
    layer: LayerKind | None = None
    ref: str | None = Field(default=None, max_length=200)


class ReplayVariantOut(ReplayIn):
    label: str


class ReplayLayerOut(BaseModel):
    """What the replay built for one Blueprint layer: the commit it was read at (or the variant's ref), whether the
    layer had uncommitted changes then, and where the version came from."""

    kind: LayerKind
    commit: str | None
    dirty: bool
    source: Literal["original", "variant", "current"]
    ref: str | None


class ReplayOut(BaseModel):
    id: int
    source_conversation_id: int
    source_run_id: int | None
    source_step_id: int
    step_position: int
    checkpoint_id: int | None
    variant: ReplayVariantOut
    # Empty for a replay stored before the layers were kept.
    blueprint: list[ReplayLayerOut]
    status: ReplayStatus
    detail: str | None
    # The replay's own archived conversation and its reply's run, whose trace is the replay's.
    conversation_id: int | None
    agent_run_id: int | None
    asked: int
    eval_case: str | None
    created_at: datetime
    finished_at: datetime | None


class ReplaySideOut(BaseModel):
    run_id: int | None
    status: str | None
    steps: int
    model_calls: int
    tool_calls: int
    tokens: int
    cost: float | None
    final_answer: str | None


class ReplayComparisonOut(BaseModel):
    replay: ReplayOut
    # The original from the replayed call on, and the replay as a whole.
    original: ReplaySideOut
    replayed: ReplaySideOut


class ReplayCaseIn(BaseModel):
    # Empty: a name from the prompt.
    name: str | None = Field(default=None, max_length=60)


class ReplayCaseOut(BaseModel):
    name: str
    path: str
    commit: str
    checkpoint_id: int | None


def replays(request: Request) -> ReplayService:
    return request.app.state.replay_service


def replay_status(value: str) -> ReplayStatus:
    for known in ("queued", "running", "done", "stopped", "error"):
        if value == known:
            return known
    return "error"


def replay_out(replay: Replay) -> ReplayOut:
    variant = stored_variant(replay.variant)
    return ReplayOut(
        id=replay.id,
        source_conversation_id=replay.source_conversation_id,
        source_run_id=replay.source_run_id,
        source_step_id=replay.source_step_id,
        step_position=replay.step_position,
        checkpoint_id=replay.checkpoint_id,
        variant=ReplayVariantOut(
            preset=variant.preset, model=variant.model, layer=variant.layer, ref=variant.ref, label=variant.label
        ),
        blueprint=[
            ReplayLayerOut(kind=layer.kind, commit=layer.commit, dirty=layer.dirty, source=layer.source, ref=layer.ref)
            for layer in replay_layers_from_json(replay.blueprint)
        ],
        status=replay_status(replay.status),
        detail=replay.detail,
        conversation_id=replay.conversation_id,
        agent_run_id=replay.agent_run_id,
        asked=replay.asked,
        eval_case=replay.eval_case,
        created_at=replay.created_at,
        finished_at=replay.finished_at,
    )


def side_out(side: SideSummary) -> ReplaySideOut:
    return ReplaySideOut(
        run_id=side.run_id,
        status=side.status,
        steps=side.steps,
        model_calls=side.model_calls,
        tool_calls=side.tool_calls,
        tokens=side.tokens,
        cost=side.cost,
        final_answer=side.final_answer,
    )


def replay_of(session: Session, conversation: Conversation, replay_id: int) -> Replay:
    replay = session.get(Replay, replay_id)
    if replay is None or replay.source_conversation_id != conversation.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Replay not found in this conversation.")
    return replay


@router.post(
    "/conversations/{conversation_id}/steps/{step_id}/replays",
    response_model=ReplayOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_replay(
    payload: ReplayIn,
    request: Request,
    conversation: ConversationDependency,
    step_id: int,
    session: SessionDependency,
) -> ReplayOut:
    """Replay the reply from this model call: what it saw, with the files of that moment, in the given variant."""
    try:
        variant = checked_variant(payload.preset, None, payload.layer, payload.ref, payload.model)
        replay_id = replays(request).start(ReplayStart(conversation.id, step_id, variant))
    except VariantError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    except ReplayNotFound as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except ReplayError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    return replay_out(session.get_one(Replay, replay_id))


@router.get("/conversations/{conversation_id}/replays", response_model=list[ReplayOut])
def list_replays(conversation: ConversationDependency, session: SessionDependency) -> list[ReplayOut]:
    """The replays of this conversation's runs, newest first."""
    rows = session.scalars(
        select(Replay).where(Replay.source_conversation_id == conversation.id).order_by(Replay.id.desc())
    )
    return [replay_out(replay) for replay in rows]


@router.get("/conversations/{conversation_id}/replays/{replay_id}", response_model=ReplayComparisonOut)
def read_replay(
    conversation: ConversationDependency, replay_id: int, session: SessionDependency
) -> ReplayComparisonOut:
    """The replay beside the original: steps, tokens, cost and final answer of each side."""
    replay = replay_of(session, conversation, replay_id)
    step = session.get(RunStep, replay.source_step_id)
    original = side_summary(session, conversation.id, replay.source_run_id, step.started_at if step else None)
    replayed = (
        side_summary(session, replay.conversation_id, replay.agent_run_id, None)
        if replay.conversation_id is not None
        else EMPTY
    )
    return ReplayComparisonOut(replay=replay_out(replay), original=side_out(original), replayed=side_out(replayed))


@router.post("/conversations/{conversation_id}/replays/{replay_id}/stop", status_code=status.HTTP_204_NO_CONTENT)
async def stop_replay(request: Request, conversation: ConversationDependency, replay_id: int) -> Response:
    """Stop a running replay; what its reply did so far is kept."""
    await replays(request).stop(replay_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/conversations/{conversation_id}/replays/{replay_id}/eval-case",
    response_model=ReplayCaseOut,
    status_code=status.HTTP_201_CREATED,
)
def make_replay_case(
    payload: ReplayCaseIn,
    conversation: ConversationDependency,
    replay_id: int,
    session: SessionDependency,
    workspace_dir: WorkspaceDirDependency,
    data_dir: DataDirDependency,
) -> ReplayCaseOut:
    """Turn the replay into an eval case that starts at its checkpoint, pinned, as one Blueprint commit."""
    replay = replay_of(session, conversation, replay_id)
    try:
        saved = case_from_replay(session, workspace_dir, data_dir, replay, payload.name)
    except CaseExistsError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except (CaseFromReplayError, ProjectError) as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except CaseWriteError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    return ReplayCaseOut(
        name=saved.case.name, path=saved.path, commit=saved.commit, checkpoint_id=saved.case.start.checkpoint
    )
