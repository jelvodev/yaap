import json
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import exists, func, select

from app.api.conversations import ConversationDependency
from app.api.dependencies import EnvironmentDependency, SessionDependency
from app.api.schemas import SearchHitOut
from app.core.export import export_filename, to_json, to_markdown
from app.storage.models import Message, SearchChunk
from app.core.search import KINDS, FoundItem, embed_query, search_everything

router = APIRouter(tags=["search"])


@router.get("/environments/{environment_id}/search", response_model=list[SearchHitOut])
def search(
    request: Request,
    environment: EnvironmentDependency,
    session: SessionDependency,
    q: str = Query(min_length=1, max_length=200),
    project_id: int | None = None,
    kind: Annotated[list[Literal["message", "memory", "knowledge", "blueprint"]] | None, Query()] = None,
    limit: int = Query(default=50, ge=1, le=50),
) -> list[SearchHitOut]:
    """Conversations, project memory and Blueprint files: full-text and, with an embedding model, by meaning."""
    kinds = tuple(kind) if kind else KINDS
    items = search_everything(
        session, environment.id, q, embed_query(getattr(request.app.state, "indexer", None), session, q), project_id=project_id, kinds=kinds, limit=limit
    )
    return [hit_out(item) for item in items]


def hit_out(item: FoundItem) -> SearchHitOut:
    level = None
    if item.kind == "blueprint":
        level = "project" if item.project_id else "environment" if item.environment_id else "global"
    return SearchHitOut(
        kind=item.kind,  # type: ignore[arg-type]
        conversation_id=item.conversation_id,
        conversation_title=item.conversation_title,
        project_id=item.project_id,
        message_id=item.message_id,
        role=item.role,
        path=item.path,
        level=level,
        snippet=item.snippet,
        created_at=item.created_at,
    )


@router.get(
    "/conversations/{conversation_id}/export",
    responses={200: {"content": {"text/markdown": {}, "application/json": {}}}},
)
def export_conversation(
    conversation: ConversationDependency,
    session: SessionDependency,
    format: Literal["markdown", "json"] = "markdown",
) -> Response:
    if conversation.head_message_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This conversation has no messages yet.")
    if format == "json":
        body = json.dumps(to_json(session, conversation), indent=2, ensure_ascii=False)
        media_type, filename = "application/json", export_filename(conversation, "json")
    else:
        body = to_markdown(session, conversation)
        media_type, filename = "text/markdown; charset=utf-8", export_filename(conversation, "md")
    return Response(
        body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "X-Content-Type-Options": "nosniff"},
    )


class SearchIndexStatusOut(BaseModel):
    # The model in use, or None when it is switched off or not available.
    model: str | None
    chunks: int
    # Complete messages the background indexing has not reached yet (0 without a model).
    waiting_messages: int


@router.get("/search-index/status", response_model=SearchIndexStatusOut)
def search_index_status(request: Request, session: SessionDependency) -> SearchIndexStatusOut:
    indexer = getattr(request.app.state, "indexer", None)
    embedder = indexer.embedder(session) if indexer is not None else None
    chunks = session.scalar(select(func.count(SearchChunk.id))) or 0
    waiting = 0
    if embedder is not None:
        waiting = (
            session.scalar(
                select(func.count(Message.id)).where(
                    Message.status == "complete",
                    ~exists(select(SearchChunk.id).where(SearchChunk.message_id == Message.id)),
                )
            )
            or 0
        )
    return SearchIndexStatusOut(model=embedder.name if embedder else None, chunks=chunks, waiting_messages=waiting)
