"""The shared document beside a conversation: open, close, and review the agent's change.

The document's text is read and saved through the Files endpoints, with their hash check and
checkpoint; these endpoints keep which document is open and the agent's change that waits for the user's review.
"""

from functools import partial
from typing import Literal

import anyio
from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.api.conversations import ConversationDependency
from app.api.dependencies import CipherDependency, SessionDependency, WorkspaceDirDependency
from app.api.files import located_files
from app.checkpoints.service import CheckpointStore
from app.documents.service import (
    DocumentError,
    clear_review,
    close_document,
    find_document,
    hash_of,
    notify_changed,
    open_document,
    read_version,
    text_of,
    unified_diff,
)
from app.files.service import ProjectFiles, name_of
from app.storage.models import Conversation, Project, SharedDocument

router = APIRouter(tags=["documents"])

MAX_DIFF_CHARS = 400_000


class DocumentReviewOut(BaseModel):
    # What the agent changed: from the version before its first change to the document as it is now.
    diff: str
    truncated: bool = False
    # The agent created the file.
    created: bool
    # The checkpoint taken just before the agent's first change; null on a computer over SSH.
    checkpoint_id: int | None


class DocumentOut(BaseModel):
    path: str
    name: str
    opened_by: Literal["user", "agent"]
    exists: bool
    # SHA-256 of the document as it is now; accepting or rolling back sends it back, so an unseen change is noticed.
    hash: str | None
    # The agent's change waiting for the user: accept it or roll it back. The panel is read-only meanwhile.
    review: DocumentReviewOut | None


class DocumentOpenIn(BaseModel):
    path: str = Field(min_length=1, max_length=1000)


class DocumentDecisionIn(BaseModel):
    # The hash of the document as the user saw it in the review.
    hash: str | None


def conversation_project(session: SessionDependency, conversation: Conversation) -> Project:
    project = session.get(Project, conversation.project_id) if conversation.project_id is not None else None
    if project is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a conversation in a project has a document.")
    return project


async def document_out(located: ProjectFiles, document: SharedDocument) -> DocumentOut:
    current = await read_version(located.files, document.path)
    review = None
    if document.review_hash is not None:
        diff = unified_diff(document.path, None if document.review_created else document.review_base, text_of(current))
        review = DocumentReviewOut(
            diff=diff[:MAX_DIFF_CHARS],
            truncated=len(diff) > MAX_DIFF_CHARS,
            created=document.review_created,
            checkpoint_id=document.review_checkpoint_id,
        )
    return DocumentOut(
        path=document.path,
        name=name_of(document.path),
        opened_by="agent" if document.opened_by == "agent" else "user",
        exists=current is not None,
        hash=hash_of(current),
        review=review,
    )


@router.get("/conversations/{conversation_id}/document", response_model=DocumentOut | None)
async def get_document(
    conversation: ConversationDependency,
    session: SessionDependency,
    cipher: CipherDependency,
    workspace_dir: WorkspaceDirDependency,
) -> DocumentOut | None:
    """The document open beside the conversation, or null."""
    document = find_document(session, conversation.id)
    if document is None or conversation.project_id is None:
        return None
    project = conversation_project(session, conversation)
    async with located_files(session, cipher, workspace_dir, project) as located:
        return await document_out(located, document)


@router.put("/conversations/{conversation_id}/document", response_model=DocumentOut)
async def put_document(
    body: DocumentOpenIn,
    conversation: ConversationDependency,
    session: SessionDependency,
    cipher: CipherDependency,
    workspace_dir: WorkspaceDirDependency,
) -> DocumentOut:
    """Open a Markdown file of the project beside the conversation, in place of the one that is open."""
    project = conversation_project(session, conversation)
    async with located_files(session, cipher, workspace_dir, project) as located:
        path = located.files.check(body.path, for_write=True)
        entry = await located.files.stat(path)
        if entry is not None and entry.is_dir:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{path} is a folder.")
        current = await located.files.read_bytes(path) if entry is not None else None
        try:
            document = open_document(session, conversation.id, path, "user", current)
        except DocumentError as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
        return await document_out(located, document)


@router.delete("/conversations/{conversation_id}/document", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(conversation: ConversationDependency, session: SessionDependency) -> Response:
    """Close the document. A change of the agent that waits for review stays as it is."""
    close_document(session, conversation.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def waiting_review(session: SessionDependency, conversation: Conversation) -> SharedDocument:
    document = find_document(session, conversation.id)
    if document is None or document.review_hash is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "There is no change of YAAP's to review.")
    return document


def refuse_if_unseen(current: bytes | None, seen: str | None) -> None:
    if hash_of(current) != seen:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "The document changed after you looked at it. Look at the change again first."
        )


@router.post("/conversations/{conversation_id}/document/accept", response_model=DocumentOut)
async def accept_change(
    body: DocumentDecisionIn,
    conversation: ConversationDependency,
    session: SessionDependency,
    cipher: CipherDependency,
    workspace_dir: WorkspaceDirDependency,
) -> DocumentOut:
    """Keep the agent's change; the document can be edited again."""
    project = conversation_project(session, conversation)
    document = waiting_review(session, conversation)
    async with located_files(session, cipher, workspace_dir, project) as located:
        refuse_if_unseen(await read_version(located.files, document.path), body.hash)
        clear_review(document)
        session.commit()
        return await document_out(located, document)


@router.post("/conversations/{conversation_id}/document/rollback", response_model=DocumentOut)
async def roll_back_change(
    body: DocumentDecisionIn,
    request: Request,
    conversation: ConversationDependency,
    session: SessionDependency,
    cipher: CipherDependency,
    workspace_dir: WorkspaceDirDependency,
) -> DocumentOut:
    """Put the document back as it was before the agent's change. In YAAP's container a checkpoint of the state
    before comes first, so the roll back can itself be undone in Checkpoints. The agent learns of it with its next
    tool result."""
    project = conversation_project(session, conversation)
    document = waiting_review(session, conversation)
    store = CheckpointStore(request.app.state.session_factory, request.app.state.data_dir)
    async with located_files(session, cipher, workspace_dir, project) as located:
        refuse_if_unseen(await read_version(located.files, document.path), body.hash)
        if located.folder is not None:
            await anyio.to_thread.run_sync(
                partial(
                    store.take,
                    project.id,
                    located.folder,
                    "document",
                    f"Before rolling back YAAP's change to {document.path}",
                    conversation_id=conversation.id,
                    kind="restore",
                )
            )
        if document.review_created:
            await located.files.remove(document.path)
        else:
            await located.files.write_bytes(document.path, (document.review_base or "").encode("utf-8"))
        clear_review(document)
        session.commit()
        notify_changed(request.app.state.runs, conversation_id=conversation.id)
        return await document_out(located, document)
