"""Working comfort around a conversation: the message queue, waiting approvals and the context breakdown."""

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.agent.runs import RunRegistry
from app.agents.definitions import agent_part, find_agent
from app.api.conversations import ConversationDependency, conversation_layers
from app.api.dependencies import (
    EnvironmentDependency,
    BlueprintDirDependency,
    ProviderRegistryDependency,
    SessionDependency,
    WorkspaceDirDependency,
)
from app.core.chat import load_branch
from app.core.message_queue import queued_items
from app.core.search import recall_project_memory
from app.core.turn_context import memory_parts, turn_context
from app.core.trimming import latest_trim, shorten
from app.core.summaries import carried_texts, cover_point, latest_summary, summary_block, yaap_compacts
from app.core.search_index import message_text
from app.core.prompts import base_prompt
from app.core.settings import resolve_languages
from app.blueprint.context import index_part, instructions_part, project_part, standards_part
from app.blueprint.layers import active_items
from app.projects.memory import read_memory
from app.projects.service import project_path
from app.storage.models import Attachment, Project, QueuedMessage, UsageRecord

router = APIRouter(tags=["activity"])

MAX_QUEUED = 20


class QueuedMessageIn(BaseModel):
    text: str = Field(default="", max_length=200_000)
    attachment_ids: list[int] = Field(default_factory=list, max_length=20)


class QueuedMessageOut(BaseModel):
    id: int
    text: str
    attachment_ids: list[int]
    created_at: datetime


class QueueTake(BaseModel):
    # One item by id, or all of them joined into one message.
    item_id: int | None = None
    all: bool = False


class QueueTaken(BaseModel):
    text: str
    attachment_ids: list[int]


def queued_to_out(item: QueuedMessage) -> QueuedMessageOut:
    return QueuedMessageOut(id=item.id, text=item.text, attachment_ids=item.attachment_ids or [], created_at=item.created_at)


@router.get("/conversations/{conversation_id}/queue", response_model=list[QueuedMessageOut])
def list_queue(conversation: ConversationDependency, session: SessionDependency) -> list[QueuedMessageOut]:
    return [queued_to_out(item) for item in queued_items(session, conversation.id)]


@router.post("/conversations/{conversation_id}/queue", response_model=QueuedMessageOut, status_code=status.HTTP_201_CREATED)
def add_to_queue(payload: QueuedMessageIn, conversation: ConversationDependency, session: SessionDependency) -> QueuedMessageOut:
    """Keep a message for when the running reply ends."""
    if not payload.text.strip() and not payload.attachment_ids:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A queued message needs text or an attachment.")
    items = queued_items(session, conversation.id)
    if len(items) >= MAX_QUEUED:
        raise HTTPException(status.HTTP_409_CONFLICT, f"At most {MAX_QUEUED} messages can wait in the queue.")
    if payload.attachment_ids:
        owned = set(
            session.scalars(
                select(Attachment.id).where(
                    Attachment.id.in_(payload.attachment_ids),
                    Attachment.conversation_id == conversation.id,
                    Attachment.message_id.is_(None),
                )
            )
        )
        if owned != set(payload.attachment_ids):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "An attachment does not belong to this conversation.")
    item = QueuedMessage(
        conversation_id=conversation.id,
        text=payload.text.strip(),
        attachment_ids=payload.attachment_ids,
        position=(max((existing.position for existing in items), default=0) + 1),
        created_at=datetime.now(UTC),
    )
    session.add(item)
    session.commit()
    return queued_to_out(item)


@router.delete("/conversations/{conversation_id}/queue/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_from_queue(item_id: int, conversation: ConversationDependency, session: SessionDependency) -> Response:
    item = session.get(QueuedMessage, item_id)
    if item is None or item.conversation_id != conversation.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That message is not in the queue.")
    session.delete(item)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/conversations/{conversation_id}/queue/take", response_model=QueueTaken)
def take_from_queue(payload: QueueTake, conversation: ConversationDependency, session: SessionDependency) -> QueueTaken:
    """Remove what is about to be sent: one item, the first one, or all joined into one message."""
    items = queued_items(session, conversation.id)
    if payload.all:
        taken = items
    elif payload.item_id is not None:
        taken = [item for item in items if item.id == payload.item_id]
    else:
        taken = items[:1]
    if not taken:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The queue is empty.")
    text = "\n\n".join(item.text for item in taken if item.text)
    attachment_ids = [attachment for item in taken for attachment in (item.attachment_ids or [])]
    for item in taken:
        session.delete(item)
    session.commit()
    return QueueTaken(text=text, attachment_ids=attachment_ids)


class SteerIn(BaseModel):
    # The text to send, or a queued message to send instead of waiting.
    text: str = Field(default="", max_length=20_000)
    item_id: int | None = None


class SteerOut(BaseModel):
    status: Literal["waiting"]


@router.post("/conversations/{conversation_id}/steer", response_model=SteerOut, status_code=status.HTTP_202_ACCEPTED)
def steer(payload: SteerIn, conversation: ConversationDependency, session: SessionDependency, request: Request) -> SteerOut:
    """Send a message to the running reply: the agent gets it with the result of its next tool call.
    When it makes no further tool call, the message is queued and sent when the reply ends."""
    registry: RunRegistry = request.app.state.runs
    run = registry.for_conversation(conversation.id) if request.app.state.replies.running(conversation.id) else None
    if run is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "No reply with tools is running; queue the message instead.")
    text = payload.text.strip()
    if payload.item_id is not None:
        item = session.get(QueuedMessage, payload.item_id)
        if item is None or item.conversation_id != conversation.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Queued message not found.")
        if item.attachment_ids:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A message with attachments waits for the reply to end.")
        text = item.text
        session.delete(item)
        session.commit()
    if not text:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A steering message needs text.")
    run.state.steering.append(text)
    return SteerOut(status="waiting")


class WaitingApprovalOut(BaseModel):
    approval_id: str
    conversation_id: int
    tool: str
    summary: str


@router.get("/environments/{environment_id}/approvals", response_model=list[WaitingApprovalOut])
def waiting_approvals(environment: EnvironmentDependency, request: Request) -> list[WaitingApprovalOut]:
    """Approvals that runs in this environment wait for, across conversations."""
    registry: RunRegistry = request.app.state.runs
    return [
        WaitingApprovalOut(
            approval_id=approval.id, conversation_id=run.conversation_id, tool=approval.tool, summary=approval.summary
        )
        for run in registry.runs.values()
        if run.environment_id == environment.id
        for approval in run.state.approvals.values()
        if not approval.future.done()
    ]


class ContextPartOut(BaseModel):
    label: str
    # Estimated from the text length (about four characters per token), as counting exactly needs a model call.
    tokens: int


class LastReplyOut(BaseModel):
    input_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    output_tokens: int
    # Share of the prompt read from the cache, 0 to 1.
    cache_share: float
    cost: float | None


class RecalledEntryOut(BaseModel):
    section: str
    text: str
    # recent: among the newest of its section; related: close to the user's message.
    reason: Literal["recent", "related"]


class MemoryRecallOut(BaseModel):
    """What of the project memory went into the prompt."""

    whole: bool
    total_entries: int
    entries: list[RecalledEntryOut]


class ContextBreakdownOut(BaseModel):
    parts: list[ContextPartOut]
    memory: MemoryRecallOut | None = None
    # Summaries: how many messages the current summary covers, and whether "Summarise now" can run.
    summarised_messages: int = 0
    can_summarise: bool = False
    context_tokens: int | None
    context_window: int | None
    last_reply: LastReplyOut | None


def estimate(text: str) -> int:
    return (len(text) + 3) // 4


@router.get("/conversations/{conversation_id}/context", response_model=ContextBreakdownOut)
def context_breakdown(
    request: Request,
    conversation: ConversationDependency,
    session: SessionDependency,
    providers: ProviderRegistryDependency,
    blueprint_dir: BlueprintDirDependency,
    workspace_dir: WorkspaceDirDependency,
) -> ContextBreakdownOut:
    """Where the context goes: each part of the system prompt, the conversation, and the last reply."""
    layers = conversation_layers(session, blueprint_dir, workspace_dir, conversation.environment_id, conversation.project_id)
    items = active_items(layers)
    agent = find_agent(layers, conversation.agent) if conversation.agent else None
    project = session.get(Project, conversation.project_id) if conversation.project_id else None
    branch = load_branch(session, conversation.id, conversation.head_message_id)
    recalled = None
    if project is not None:
        last_question = next((message_text(message) for message in reversed(branch) if message.role == "user"), "")
        recalled = recall_project_memory(
            session,
            getattr(request.app.state, "indexer", None),
            conversation.environment_id,
            project.id,
            read_memory(project_path(workspace_dir, project)),
            last_question,
        )
    texts = [
        ("Base prompt", base_prompt(resolve_languages(session, conversation.environment_id, conversation.project_id))),
        ("General instructions", instructions_part(items) or ""),
        ("Standards", standards_part(items) or ""),
        ("Skills, commands and agents", index_part(items) or ""),
        ("Agent role", agent_part(agent, layers) if agent else ""),
        ("Project and memory", project_part(project, memory_parts(recalled)[0]) if project and recalled else ""),
        ("Context with the message (date, recalled memory)", turn_context(session, memory_parts(recalled)[1] if recalled else None)),
    ]
    parts = [ContextPartOut(label=label, tokens=estimate(text)) for label, text in texts if text]
    skill_text = "".join(
        block.get("text", "") for message in branch for block in message.content if block.get("type") == "skill"
    )
    if skill_text:
        parts.append(ContextPartOut(label="Pinned skills", tokens=estimate(skill_text)))
    summary_index = latest_summary(branch)
    summarised_messages = 0
    if summary_index is not None:
        block = summary_block(branch[summary_index]) or {}
        summarised_messages = summary_index + 1
        parts.append(ContextPartOut(label=f"Summary of {summarised_messages} earlier messages", tokens=estimate(block.get("text", ""))))
        kept_texts = carried_texts(branch[: summary_index + 1])
        if kept_texts:
            parts.append(ContextPartOut(label="Skills kept after the summary", tokens=estimate("".join(kept_texts))))
    history = ""
    trim_index = latest_trim(branch)
    if trim_index is not None and (summary_index is None or trim_index > summary_index):
        parts.append(ContextPartOut(label=f"Old tool output shortened up to message {trim_index + 1}", tokens=0))
    start = (summary_index + 1) if summary_index is not None else 0
    for position, message in enumerate(branch[start:], start):
        trimmed = trim_index is not None and position <= trim_index
        for block in message.content:
            kind = block.get("type")
            if kind in ("text", "command"):
                history += block.get("text", "")
            elif kind == "tool_call":
                output = block.get("output") or ""
                history += f"{block.get('input', '')}{shorten(output) if trimmed else output}"
    attachments = session.scalar(
        select(func.count()).select_from(Attachment).where(Attachment.conversation_id == conversation.id, Attachment.message_id.is_not(None))
    )
    parts.append(ContextPartOut(label="Conversation so far", tokens=estimate(history)))
    if attachments:
        # Images and documents are counted by the provider, not by their text; shown as a rough figure.
        parts.append(ContextPartOut(label=f"Attachments ({attachments})", tokens=attachments * 1500))

    last = session.scalar(
        select(UsageRecord)
        .where(UsageRecord.conversation_id == conversation.id, UsageRecord.purpose == "main")
        .order_by(UsageRecord.id.desc())
    )
    last_reply = None
    if last is not None:
        prompt = last.input_tokens + last.cache_read_tokens + last.cache_write_tokens
        last_reply = LastReplyOut(
            input_tokens=last.input_tokens,
            cache_read_tokens=last.cache_read_tokens,
            cache_write_tokens=last.cache_write_tokens,
            output_tokens=last.output_tokens,
            cache_share=round(last.cache_read_tokens / prompt, 3) if prompt else 0.0,
            cost=last.cost,
        )
    model = providers.find_model(conversation.model)
    context_tokens = None
    if last is not None:
        context_tokens = last.context_tokens if last.context_tokens is not None else (
            last.input_tokens + last.cache_read_tokens + last.cache_write_tokens + last.output_tokens
        )
    memory = None
    if recalled is not None and recalled.total:
        memory = MemoryRecallOut(
            whole=recalled.whole,
            total_entries=recalled.total,
            entries=[RecalledEntryOut(section=e.section, text=e.text, reason="recent") for e in recalled.recent]
            + [RecalledEntryOut(section=e.section, text=e.text, reason="related") for e in recalled.related],
        )
    return ContextBreakdownOut(
        parts=parts,
        memory=memory,
        summarised_messages=summarised_messages,
        can_summarise=yaap_compacts(session, providers, conversation) and cover_point(branch) is not None,
        context_tokens=context_tokens,
        context_window=model.context_window if model else None,
        last_reply=last_reply,
    )
