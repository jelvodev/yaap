import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import (
    EnvironmentDependency,
    FilesDirDependency,
    BlueprintDirDependency,
    ProviderRegistryDependency,
    SessionDependency,
    WorkspaceDirDependency,
)
from app.api.schemas import (
    ContextControlOut,
    ConversationCreate,
    ConversationOut,
    ConversationSummaryOut,
    ConversationTargetOut,
    BranchSwitch,
    ConversationMove,
    ConversationUpdate,
    GoalOut,
    GoalUpdate,
    ImportedFromOut,
    MessageCreate,
    UtilityEventOut,
    DefaultSkillsOut,
    PinnedSkillsIn,
    message_to_out,
)
from app.agent.outputs import OutputStore, remove_conversation_outputs
from app.agent.shells import remove_conversation_shells
from app.agent.runs import RunRegistry
from app.agents.definitions import find_agent
from app.core.conversation_move import MoveBusy, MoveError, move_conversation
from app.core.conversations import delete_conversation
from app.core.goals import ACTIVE, end_goal, goal_cost, latest_goal, open_goal
from app.core.chat_history import DEFAULT_TITLE, load_branch
from app.core.replies import ReplyRegistry
from app.core.reply_errors import InvalidReplyRequest, ReplyBusy, ReplyConflict, ReplyError
from app.core.reply_service import NewMessage, ReplyService
from app.core.reply_services import ReplyServices
from app.core.context_control import ContextControl, check_conversation_limit, resolve_context_control
from app.core.summaries import MAX_FOCUS_CHARS, SummaryError, summarise, yaap_compacts
from app.core.message_parts import MessagePartError, active_of_kind, check_skills
from app.core.rule_proposal import RuleProposalError, draft_rule
from app.core.settings import SETTING_DEFINITIONS, resolve_setting
from app.core.utility import UtilityContext
from app.blueprint.layers import Layer
from app.blueprint.levels import layers_for
from app.mcp_client.runs import server_place
from app.browser.service import remove_conversation_browser, shots_dir
from app.storage.models import Conversation, Environment, Goal, Message, Project, Target, UsageRecord
from app.targets.base import TargetError
from app.targets.service import allowed_access, chosen_target

logger = logging.getLogger(__name__)

router = APIRouter(tags=["conversations"])


def summary_out(conversation: Conversation) -> ConversationSummaryOut:
    return ConversationSummaryOut(
        id=conversation.id,
        project_id=conversation.project_id,
        title=conversation.title,
        provider=conversation.provider,
        model=conversation.model,
        effort=conversation.effort,
        agent=conversation.agent,
        pinned_skills=conversation.pinned_skills or [],
        updated_at=conversation.updated_at,
        archived_at=conversation.archived_at,
        scheduled=conversation.scheduled_task_id is not None,
        unread=bool(conversation.unread),
        access=conversation.access or "default",  # type: ignore[arg-type]
        target_id=conversation.target_id,
        context_limit=conversation.context_limit,
        imported=ImportedFromOut(**conversation.imported_from) if conversation.imported_from else None,
    )


def context_control_out(control: ContextControl) -> ContextControlOut:
    return ContextControlOut(
        limit=control.limit,
        source=control.source,  # type: ignore[arg-type]
        auto_compact=control.auto_compact,
        compact_at=control.compact_at,
        trim_at=control.trim_at,
        compacts_itself=control.compacts_itself,
    )


def target_of(session: Session, conversation: Conversation) -> ConversationTargetOut | None:
    try:
        chosen = chosen_target(session, conversation)
    except TargetError:
        return None
    if chosen is None:
        return None
    target, folder = chosen
    return ConversationTargetOut(
        id=target.id,
        name=target.name,
        system=target.system,
        rights=target.rights,
        folder=folder,
        allow_full_access=target.allow_full_access,
    )


def check_target_choice(session: Session, environment_id: int, target_id: int | None) -> int | None:
    if target_id is None:
        return None
    target = session.get(Target, target_id)
    # A project's sandbox is chosen in the project, not per conversation.
    if target is None or target.environment_id != environment_id or target.kind != "ssh":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown target in this environment.")
    return target.id


def check_full_access(target: ConversationTargetOut | None) -> None:
    if target is not None and not target.allow_full_access:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Full access is off for {target.name}. Switch on Allow full access for it in Settings › Targets first.",
        )


def conversation_layers(
    session: Session, blueprint_dir: Path, workspace_dir: Path, environment_id: int, project_id: int | None
) -> list[Layer]:
    environment = session.get_one(Environment, environment_id)
    project = session.get(Project, project_id) if project_id else None
    return layers_for(blueprint_dir, workspace_dir, environment, project)


def check_agent(layers: list[Layer], name: str | None) -> str | None:
    if name is None or not name.strip():
        return None
    if find_agent(layers, name.strip()) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"There is no agent named {name.strip()}.")
    return name.strip()


def get_conversation(conversation_id: int, session: SessionDependency) -> Conversation:
    conversation = session.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found.")
    return conversation


ConversationDependency = Annotated[Conversation, Depends(get_conversation)]


def reply_service(request: Request) -> ReplyService:
    return ReplyService(ReplyServices.from_state(request.app.state))


ReplyServiceDependency = Annotated[ReplyService, Depends(reply_service)]

# The status code for each kind of reply error; an error takes the code of its nearest listed class.
REPLY_ERROR_STATUS: dict[type[ReplyError], int] = {
    ReplyConflict: status.HTTP_409_CONFLICT,
    InvalidReplyRequest: status.HTTP_422_UNPROCESSABLE_CONTENT,
    ReplyError: status.HTTP_409_CONFLICT,
}


def reply_http_error(error: ReplyError) -> HTTPException:
    code = next(REPLY_ERROR_STATUS[kind] for kind in type(error).__mro__ if kind in REPLY_ERROR_STATUS)
    return HTTPException(code, str(error))


@router.get("/environments/{environment_id}/conversations", response_model=list[ConversationSummaryOut])
def list_conversations(
    environment: EnvironmentDependency, session: SessionDependency, archived: bool = False
) -> list[ConversationSummaryOut]:
    """The environment's conversations, newest first; with archived=true only the archived ones."""
    archived_filter = Conversation.archived_at.is_not(None) if archived else Conversation.archived_at.is_(None)
    conversations = session.scalars(
        select(Conversation)
        .where(Conversation.environment_id == environment.id, archived_filter)
        .order_by(Conversation.updated_at.desc())
    )
    return [summary_out(conversation) for conversation in conversations]


@router.get("/environments/{environment_id}/default-skills", response_model=DefaultSkillsOut)
def default_skills(
    environment: EnvironmentDependency,
    session: SessionDependency,
    blueprint_dir: BlueprintDirDependency,
    workspace_dir: WorkspaceDirDependency,
    project_id: int | None = None,
) -> DefaultSkillsOut:
    """The skills a new conversation here starts with pinned."""
    if project_id is not None:
        project = session.get(Project, project_id)
        if project is None or project.environment_id != environment.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found in this environment.")
    names = resolve_setting(session, SETTING_DEFINITIONS["default_skills"], environment.id, project_id).value or []
    available = active_of_kind(conversation_layers(session, blueprint_dir, workspace_dir, environment.id, project_id), "skill")
    return DefaultSkillsOut(
        skills=[name for name in names if name in available],
        missing=[name for name in names if name not in available],
    )


@router.post(
    "/environments/{environment_id}/conversations",
    response_model=ConversationSummaryOut,
    status_code=status.HTTP_201_CREATED,
)
def create_conversation(
    payload: ConversationCreate,
    environment: EnvironmentDependency,
    session: SessionDependency,
    providers: ProviderRegistryDependency,
    blueprint_dir: BlueprintDirDependency,
    workspace_dir: WorkspaceDirDependency,
) -> ConversationSummaryOut:
    model = providers.find_model(payload.model)
    if model is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown model.")
    if payload.effort not in model.efforts:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Effort must be one of: {', '.join(model.efforts)}.")
    if (problem := check_conversation_limit(providers, model.id, payload.context_limit)) is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, problem)
    if payload.project_id is not None:
        project = session.get(Project, payload.project_id)
        if project is None or project.environment_id != environment.id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown project in this environment.")
        if payload.target_id is not None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "A project conversation works where its project settings say."
            )
    target_id = check_target_choice(session, environment.id, payload.target_id)
    agent = None
    pinned: list[str] = []
    if payload.agent or payload.pinned_skills:
        layers = conversation_layers(session, blueprint_dir, workspace_dir, environment.id, payload.project_id)
        agent = check_agent(layers, payload.agent)
        try:
            pinned = check_skills(layers, payload.pinned_skills)
        except MessagePartError as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    now = datetime.now(UTC)
    conversation = Conversation(
        environment_id=environment.id,
        project_id=payload.project_id,
        agent=agent,
        pinned_skills=pinned,
        preset=" ".join(payload.preset.split()) if payload.preset else None,
        title=DEFAULT_TITLE,
        provider=model.provider,
        model=model.id,
        effort=payload.effort,
        access=payload.access,
        target_id=target_id,
        context_limit=payload.context_limit,
        created_at=now,
        updated_at=now,
    )
    if payload.access == "full":
        check_full_access(target_of(session, conversation))
    session.add(conversation)
    session.commit()
    return summary_out(conversation)


@router.get("/conversations/{conversation_id}", response_model=ConversationOut)
def read_conversation(
    conversation: ConversationDependency, session: SessionDependency, providers: ProviderRegistryDependency
) -> ConversationOut:
    if conversation.unread:
        conversation.unread = False  # A finished task run is seen once its conversation is opened.
        session.commit()
    branch = load_branch(session, conversation.id, conversation.head_message_id)
    latest_usage = session.scalar(
        select(UsageRecord).where(UsageRecord.conversation_id == conversation.id).order_by(UsageRecord.id.desc())
    )
    model = providers.find_model(conversation.model)
    versions: dict[tuple[int | None, str], list[int]] = {}
    for message_id, parent_id, role in session.execute(
        select(Message.id, Message.parent_id, Message.role)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.id)
    ):
        versions.setdefault((parent_id, role), []).append(message_id)
    return ConversationOut(
        **summary_out(conversation).model_dump(),
        environment_id=conversation.environment_id,
        target=target_of(session, conversation),
        messages=[
            message_to_out(message, versions.get((message.parent_id, message.role), [message.id]))
            for message in branch
        ],
        # Everything of the last turn is in the context of the next one.
        context_tokens=(
            None
            if latest_usage is None
            else latest_usage.context_tokens
            if latest_usage.context_tokens is not None
            else latest_usage.input_tokens
            + latest_usage.cache_read_tokens
            + latest_usage.cache_write_tokens
            + latest_usage.output_tokens
        ),
        context_window=model.context_window if model else None,
        context_control=context_control_out(resolve_context_control(session, providers, conversation)),
        utility_events=[
            UtilityEventOut(
                task=record.task,
                summary=record.summary,
                model=record.model,
                input_tokens=record.input_tokens + record.cache_read_tokens + record.cache_write_tokens,
                output_tokens=record.output_tokens,
                cost=record.cost,
                created_at=record.created_at,
            )
            for record in session.scalars(
                select(UsageRecord)
                .where(
                    UsageRecord.conversation_id == conversation.id,
                    UsageRecord.purpose == "utility",
                    UsageRecord.task.is_not(None),
                )
                .order_by(UsageRecord.id)
            )
        ],
    )


@router.patch("/conversations/{conversation_id}", response_model=ConversationSummaryOut)
def update_conversation(
    request: Request,
    payload: ConversationUpdate,
    conversation: ConversationDependency,
    session: SessionDependency,
    providers: ProviderRegistryDependency,
    blueprint_dir: BlueprintDirDependency,
    workspace_dir: WorkspaceDirDependency,
) -> ConversationSummaryOut:
    """Rename, archive or restore the conversation, change the agent it works as or its context limit from the next
    message on, or change its access level at once."""
    if payload.title is not None:
        title = payload.title.strip()
        if not title:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The title cannot be empty.")
        conversation.title = title
    if payload.archived is not None:
        conversation.archived_at = datetime.now(UTC) if payload.archived else None
    if "agent" in payload.model_fields_set:
        layers = conversation_layers(
            session, blueprint_dir, workspace_dir, conversation.environment_id, conversation.project_id
        )
        conversation.agent = check_agent(layers, payload.agent)
    if "target_id" in payload.model_fields_set:
        if conversation.project_id is not None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "A project conversation works where its project settings say."
            )
        if session.scalar(select(Message.id).where(Message.conversation_id == conversation.id).limit(1)) is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Where a conversation works is chosen before its first message; start a new one."
            )
        conversation.target_id = check_target_choice(session, conversation.environment_id, payload.target_id)
    if "context_limit" in payload.model_fields_set:
        if (problem := check_conversation_limit(providers, conversation.model, payload.context_limit)) is not None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, problem)
        conversation.context_limit = payload.context_limit
    if payload.access is not None:
        target = target_of(session, conversation)
        if payload.access == "full":
            check_full_access(target)
        conversation.access = payload.access
        # A reply that runs now gets the new level for its next tool call, its sub-runs included.
        runs: RunRegistry = request.app.state.runs
        for run in list(runs.runs.values()):
            if run.conversation_id == conversation.id:
                stored = session.get(Target, target.id) if target is not None else None
                run.change_access(allowed_access(payload.access, stored, run.unattended))
    session.commit()
    return summary_out(conversation)


@router.post("/conversations/{conversation_id}/move", response_model=ConversationSummaryOut)
async def move_to_project(
    request: Request, payload: ConversationMove, conversation: ConversationDependency, session: SessionDependency
) -> ConversationSummaryOut:
    """Move the conversation to another project of its environment, or out of its project. Refused while it works;
    its processes and shell sessions stop. Files and checkpoints stay with the old project."""
    target = session.get(Project, payload.project_id) if payload.project_id is not None else None
    if payload.project_id is not None and target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found.")
    state = request.app.state
    try:
        await move_conversation(
            session, state.replies, state.runs, state.processes, state.shells, conversation, target
        )
    except MoveBusy as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except MoveError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    return summary_out(conversation)


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_conversation(
    request: Request, conversation: ConversationDependency, session: SessionDependency, files_dir: FilesDirDependency
) -> None:
    """Delete the conversation for good. Its usage stays counted."""
    replies: ReplyRegistry = request.app.state.replies
    if replies.busy(conversation.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "YAAP is still replying in this conversation; stop it first.")
    conversation_id = conversation.id
    browser_place = server_place(conversation.environment_id).place
    delete_conversation(session, files_dir, conversation)
    remove_conversation_outputs(request.app.state.data_dir, conversation_id)
    remove_conversation_shells(request.app.state.data_dir, conversation_id)
    anyio.from_thread.run(
        remove_conversation_browser, request.app.state.mcp, request.app.state.data_dir, conversation_id, browser_place
    )


@router.get("/conversations/{conversation_id}/browser/shots/{number}")
def browser_shot(request: Request, conversation: ConversationDependency, number: int) -> FileResponse:
    """A screenshot the agent's browser took in this conversation."""
    folder = shots_dir(request.app.state.data_dir, conversation.id)
    found = next((path for path in folder.glob(f"{int(number)}.*")), None) if folder.is_dir() else None
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Screenshot not found.")
    return FileResponse(found, headers={"Cache-Control": "private, max-age=86400"})


@router.get("/conversations/{conversation_id}/outputs/{number}", response_class=PlainTextResponse)
def read_full_output(request: Request, conversation: ConversationDependency, number: int) -> PlainTextResponse:
    """The whole output of a tool call that reached the model shortened."""
    text = OutputStore(request.app.state.data_dir, conversation.id).read(number)
    if text is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This output is gone; outputs are kept for 30 days.")
    return PlainTextResponse(text)


@router.put("/conversations/{conversation_id}/pinned-skills", response_model=ConversationSummaryOut)
def pin_skills(
    payload: PinnedSkillsIn,
    conversation: ConversationDependency,
    session: SessionDependency,
    blueprint_dir: BlueprintDirDependency,
    workspace_dir: WorkspaceDirDependency,
) -> ConversationSummaryOut:
    """The skills pinned in this conversation; changes reach the model with the next message."""
    layers = conversation_layers(
        session, blueprint_dir, workspace_dir, conversation.environment_id, conversation.project_id
    )
    try:
        conversation.pinned_skills = check_skills(layers, payload.skills)
    except MessagePartError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    session.commit()
    return summary_out(conversation)


def utility_context(request: Request) -> UtilityContext:
    state = request.app.state
    return UtilityContext(
        state.session_factory, state.cipher, state.credential_types, state.providers, state.blueprint_dir
    )


@router.post(
    "/conversations/{conversation_id}/messages",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}, "description": "Server-sent events with the reply."}},
)
def send_message(
    payload: MessageCreate,
    conversation: ConversationDependency,
    session: SessionDependency,
    service: ReplyServiceDependency,
) -> StreamingResponse:
    """Send a message after the current head, or, with edit_of, as a new version of an earlier message."""
    message = NewMessage(payload.text, payload.edit_of, tuple(payload.attachment_ids))
    try:
        stream = service.send(session, conversation, message)
    except ReplyError as error:
        raise reply_http_error(error) from error
    return event_stream(stream.events)


@router.post(
    "/conversations/{conversation_id}/messages/{message_id}/retry",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}, "description": "The reply to the stored message."}},
)
def retry_message(
    message_id: int, conversation: ConversationDependency, session: SessionDependency, service: ReplyServiceDependency
) -> StreamingResponse:
    """Retry a reply start against its existing latest user message, without storing the message twice."""
    try:
        job = service.retry_user_message(session, conversation, message_id)
    except ReplyError as error:
        raise reply_http_error(error) from error
    return event_stream(job.follow())


@router.post(
    "/conversations/{conversation_id}/messages/{message_id}/regenerate",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}, "description": "Server-sent events with the new reply."}},
)
def regenerate_reply(
    message_id: int, conversation: ConversationDependency, session: SessionDependency, service: ReplyServiceDependency
) -> StreamingResponse:
    """A new version of a reply: the same question answered again, next to the earlier answer."""
    try:
        job = service.regenerate(session, conversation, message_id)
    except ReplyError as error:
        raise reply_http_error(error) from error
    return event_stream(job.follow())


@router.put("/conversations/{conversation_id}/head", response_model=ConversationOut)
def switch_branch(
    payload: BranchSwitch,
    request: Request,
    conversation: ConversationDependency,
    session: SessionDependency,
    providers: ProviderRegistryDependency,
) -> ConversationOut:
    """Show another version of a message: the conversation continues from its newest follow-up."""
    if request.app.state.replies.busy(conversation.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Wait until YAAP has finished replying.")
    messages = list(session.scalars(select(Message).where(Message.conversation_id == conversation.id)))
    if not any(message.id == payload.message_id for message in messages):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That message is not part of this conversation.")
    newest_child: dict[int, int] = {}
    for message in messages:
        if message.parent_id is not None:
            newest_child[message.parent_id] = max(newest_child.get(message.parent_id, 0), message.id)
    head = payload.message_id
    while head in newest_child:
        head = newest_child[head]
    conversation.head_message_id = head
    session.commit()
    return read_conversation(conversation, session, providers)


def event_stream(events: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(
        events,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get(
    "/conversations/{conversation_id}/reply",
    response_class=StreamingResponse,
    responses={
        200: {"content": {"text/event-stream": {}}, "description": "The running reply, from its first event."},
        204: {"description": "No reply is running."},
    },
)
def follow_reply(conversation: ConversationDependency, request: Request) -> Response:
    job = request.app.state.replies.running(conversation.id)
    if job is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return event_stream(job.follow())


@router.post("/conversations/{conversation_id}/reply/stop", status_code=status.HTTP_204_NO_CONTENT)
async def stop_reply(conversation_id: int, request: Request) -> Response:
    """Stop the running reply; what arrived so far is stored as incomplete. Nothing running is not an error."""
    await request.app.state.replies.stop(conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/environments/{environment_id}/replies", response_model=list[int])
def running_replies(environment: EnvironmentDependency, request: Request) -> list[int]:
    """The conversations of the environment with a reply in progress."""
    return request.app.state.replies.running_in(environment.id)


# --- Goal mode -------------------------------------------------------------------------------------


def goal_out(session: Session, goal: Goal) -> GoalOut:
    return GoalOut(
        id=goal.id,
        conversation_id=goal.conversation_id,
        objective=goal.objective,
        status=goal.status,  # type: ignore[arg-type]
        max_turns=goal.max_turns,
        max_cost=goal.max_cost,
        turns=goal.turns,
        cost=round(goal_cost(session, goal), 4),
        summary=goal.summary,
        created_at=goal.created_at,
        finished_at=goal.finished_at,
    )


@router.get("/conversations/{conversation_id}/goal", response_model=GoalOut | None)
def read_goal(conversation: ConversationDependency, session: SessionDependency) -> GoalOut | None:
    """The conversation's latest goal, open or ended; null when it never had one."""
    goal = latest_goal(session, conversation.id)
    return goal_out(session, goal) if goal is not None else None


@router.patch("/conversations/{conversation_id}/goal", response_model=GoalOut)
def change_goal(
    payload: GoalUpdate,
    conversation: ConversationDependency,
    session: SessionDependency,
    service: ReplyServiceDependency,
) -> GoalOut:
    """Pause, resume or stop the open goal, or change its limits. Resuming sends the next turn when nothing runs."""
    goal = open_goal(session, conversation.id)
    if goal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This conversation has no open goal.")
    resume = payload.status == ACTIVE and goal.status != ACTIVE
    reservation = None
    if resume:
        try:
            reservation = service.reserve(conversation.id)
        except ReplyBusy as error:
            raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    try:
        if payload.max_turns is not None:
            goal.max_turns = payload.max_turns
        if payload.max_cost is not None:
            goal.max_cost = payload.max_cost
        if payload.status == "stopped":
            end_goal(goal, "stopped", "Stopped by you.")
        elif payload.status is not None:
            goal.status = payload.status
        session.commit()
        if resume and reservation is not None:
            service.send_goal_turn(conversation.id, resuming=True, reservation=reservation)
            session.expire_all()
        return goal_out(session, session.get_one(Goal, goal.id))
    finally:
        if reservation is not None:
            reservation.release()


# --- Remember as rule ------------------------------------------------------------------------------


class RuleProposalOut(BaseModel):
    rule: str
    level: Literal["global", "environment", "project"]
    path: str | None
    # False when the utility model was not available and the proposal is the user's own text.
    from_model: bool
    # Why no model drafted it, when the model policy skipped the task.
    skipped: str | None = None


@router.post("/conversations/{conversation_id}/messages/{message_id}/rule-proposal", response_model=RuleProposalOut)
async def propose_rule(
    message_id: int,
    request: Request,
    conversation: ConversationDependency,
    session: SessionDependency,
    blueprint_dir: BlueprintDirDependency,
    workspace_dir: WorkspaceDirDependency,
) -> RuleProposalOut:
    """A rule proposed from one of the user's messages; the user reviews it before anything is written."""
    try:
        draft = await draft_rule(
            utility_context(request), session, conversation, message_id, blueprint_dir, workspace_dir
        )
    except RuleProposalError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    return RuleProposalOut(
        rule=draft.rule, level=draft.level, path=draft.path, from_model=draft.from_model, skipped=draft.skipped
    )


class CompactIn(BaseModel):
    # What the summary should keep in detail, as with /compact <focus>.
    focus: str | None = Field(default=None, max_length=MAX_FOCUS_CHARS)


class SummaryOut(BaseModel):
    # The last message the summary covers, and how many messages of the branch it covers.
    message_id: int
    covered: int


@router.post("/conversations/{conversation_id}/summary", response_model=SummaryOut)
async def summarise_now(
    request: Request,
    conversation: ConversationDependency,
    session: SessionDependency,
    providers: ProviderRegistryDependency,
    payload: CompactIn | None = None,
) -> SummaryOut:
    """Summarise the earlier messages now, instead of waiting until the context fills up; with a focus
    as /compact does."""
    if request.app.state.replies.busy(conversation.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Wait until YAAP has finished replying.")
    if not yaap_compacts(session, providers, conversation):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Claude Code and Codex keep their own sessions short; YAAP does not summarise them."
        )
    conversation_id = conversation.id
    session.close()
    try:
        focus = " ".join((payload.focus or "").split()) if payload else ""
        result = await summarise(utility_context(request), conversation_id, focus or None)
    except SummaryError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    return SummaryOut(message_id=result.covered_message_id, covered=result.covered)
