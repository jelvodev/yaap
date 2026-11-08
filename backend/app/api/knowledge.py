"""Project knowledge: reference documents the agent can search and read."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.api.dependencies import ProjectDependency, SessionDependency, WorkspaceDirDependency
from app.attachments.ocr import OCR_NOTE
from app.knowledge.service import (
    KnowledgeError,
    MAX_INDEXED_CHARS,
    add_document,
    add_page,
    project_documents,
    read_text,
    refresh_page,
    remove_document,
    sync_knowledge_folder,
)
from app.projects.service import project_path
from app.storage.models import KnowledgeDocument, SearchChunk

router = APIRouter(tags=["knowledge"])


class KnowledgeDocumentOut(BaseModel):
    id: int
    name: str
    kind: str
    size: int
    chars: int
    created_at: datetime
    # waiting: not in the index yet; indexed: searchable by words and meaning; words: by words only (no model).
    index_state: Literal["waiting", "indexed", "words"]
    # Only the first part of a very long document is in the index; read_knowledge reads all of it.
    partly_indexed: bool
    # Where it comes from (a web page or the knowledge folder), and whether OCR read (part of) it.
    source_url: str | None = None
    source_path: str | None = None
    fetched_at: datetime | None = None
    ocr: bool = False


class PageIn(BaseModel):
    url: str = Field(min_length=8, max_length=2000)


class SyncOut(BaseModel):
    changed: int


def index_states(session: SessionDependency, documents: list[KnowledgeDocument]) -> dict[int, str]:
    keys = {f"knowledge:{document.id}": document.id for document in documents}
    states: dict[int, str] = {}
    if not keys:
        return states
    rows = session.execute(
        select(SearchChunk.source_key, SearchChunk.embedding.is_not(None)).where(SearchChunk.source_key.in_(keys))
    )
    for key, embedded in rows:
        document_id = keys[key]
        if embedded:
            states[document_id] = "indexed"
        else:
            states.setdefault(document_id, "words")
    return states


def document_out(document: KnowledgeDocument, state: str, ocr: bool = False) -> KnowledgeDocumentOut:
    return KnowledgeDocumentOut(
        id=document.id,
        name=document.name,
        kind=document.kind,
        size=document.size,
        chars=document.chars,
        created_at=document.created_at,
        index_state=state,  # type: ignore[arg-type]
        partly_indexed=document.chars > MAX_INDEXED_CHARS,
        source_url=document.source_url,
        source_path=document.source_path,
        fetched_at=document.fetched_at,
        ocr=ocr,
    )


def nudge(request: Request) -> None:
    indexer = getattr(request.app.state, "indexer", None)
    if indexer is not None:
        indexer.nudge()


@router.get("/projects/{project_id}/knowledge", response_model=list[KnowledgeDocumentOut])
def list_knowledge(project: ProjectDependency, session: SessionDependency, request: Request) -> list[KnowledgeDocumentOut]:
    documents = project_documents(session, project.id)
    states = index_states(session, documents)
    return [
        document_out(document, states.get(document.id, "waiting"), read_with_ocr(request.app.state.data_dir, document))
        for document in documents
    ]


def read_with_ocr(data_dir, document: KnowledgeDocument) -> bool:  # type: ignore[no-untyped-def]
    if document.kind not in ("pdf", "image"):
        return False
    return OCR_NOTE in read_text(data_dir, document)[:200_000]


def find(session: SessionDependency, project_id: int, document_id: int) -> KnowledgeDocument:
    document = session.get(KnowledgeDocument, document_id)
    if document is None or document.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.")
    return document


@router.post("/projects/{project_id}/knowledge/web", response_model=KnowledgeDocumentOut, status_code=status.HTTP_201_CREATED)
def add_web_page(payload: PageIn, request: Request, project: ProjectDependency, session: SessionDependency) -> KnowledgeDocumentOut:
    """Keep a web page's text as knowledge; it can be fetched again with refresh."""
    try:
        document = add_page(session, request.app.state.data_dir, project, payload.url)
    except KnowledgeError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    nudge(request)
    return document_out(document, "waiting")


@router.post("/projects/{project_id}/knowledge/{document_id}/refresh", response_model=KnowledgeDocumentOut)
def refresh_web_page(document_id: int, request: Request, project: ProjectDependency, session: SessionDependency) -> KnowledgeDocumentOut:
    document = find(session, project.id, document_id)
    try:
        refresh_page(session, request.app.state.data_dir, document)
    except KnowledgeError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    nudge(request)
    states = index_states(session, [document])
    return document_out(document, states.get(document.id, "waiting"))


@router.post("/projects/{project_id}/knowledge/sync", response_model=SyncOut)
def sync_folder_now(request: Request, project: ProjectDependency, session: SessionDependency, workspace_dir: WorkspaceDirDependency) -> SyncOut:
    """Read the knowledge folder now instead of at the next index pass."""
    changed = sync_knowledge_folder(session, request.app.state.data_dir, project, project_path(workspace_dir, project))
    nudge(request)
    return SyncOut(changed=changed)


@router.post("/projects/{project_id}/knowledge", response_model=KnowledgeDocumentOut, status_code=status.HTTP_201_CREATED)
def upload_knowledge(
    file: UploadFile, request: Request, project: ProjectDependency, session: SessionDependency
) -> KnowledgeDocumentOut:
    try:
        document = add_document(session, request.app.state.data_dir, project, file.filename or "document", file.file)
    except KnowledgeError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    nudge(request)
    return document_out(document, "waiting")


@router.delete("/projects/{project_id}/knowledge/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_knowledge(
    document_id: int, request: Request, project: ProjectDependency, session: SessionDependency
) -> Response:
    document = session.get(KnowledgeDocument, document_id)
    if document is None or document.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.")
    session.execute(delete(SearchChunk).where(SearchChunk.source_key == f"knowledge:{document.id}"))
    remove_document(session, request.app.state.data_dir, document)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
