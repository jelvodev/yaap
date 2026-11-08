"""Attachments: upload to a conversation, read back, and remove before sending."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from app.api.conversations import ConversationDependency
from app.api.dependencies import FilesDirDependency, SessionDependency
from app.api.schemas import AttachmentOut
from app.attachments.files import AttachmentError, original_file
from app.attachments.service import (
    attachment_folder,
    attachment_summary,
    create_attachment,
    delete_attachment,
)
from app.storage.models import Attachment

router = APIRouter(tags=["attachments"])

# Only raster images are shown in the browser; everything else is downloaded, so an uploaded HTML or SVG file
# never runs as a page of YAAP.
INLINE_MEDIA_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


def get_attachment(attachment_id: int, session: SessionDependency) -> Attachment:
    attachment = session.get(Attachment, attachment_id)
    if attachment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found.")
    return attachment


AttachmentDependency = Annotated[Attachment, Depends(get_attachment)]


def attachment_out(attachment: Attachment) -> AttachmentOut:
    return AttachmentOut(
        id=attachment.id,
        conversation_id=attachment.conversation_id,
        message_id=attachment.message_id,
        filename=attachment.filename,
        media_type=attachment.media_type,
        kind=attachment.kind,
        size=attachment.size,
        details=attachment_summary(attachment),
        created_at=attachment.created_at,
    )


@router.post(
    "/conversations/{conversation_id}/attachments",
    response_model=AttachmentOut,
    status_code=status.HTTP_201_CREATED,
)
def upload_attachment(
    file: UploadFile,
    conversation: ConversationDependency,
    session: SessionDependency,
    files_dir: FilesDirDependency,
    kind: Annotated[Literal["file", "folder"], Form()] = "file",
) -> AttachmentOut:
    """Add a file, or a folder the browser packed as a zip (kind folder): one attachment with its paths."""
    try:
        attachment = create_attachment(
            session, files_dir, conversation, file.filename or "file", file.file, as_folder=kind == "folder"
        )
    except AttachmentError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    return attachment_out(attachment)


@router.get("/attachments/{attachment_id}", response_model=AttachmentOut)
def read_attachment(attachment: AttachmentDependency) -> AttachmentOut:
    return attachment_out(attachment)


@router.get("/attachments/{attachment_id}/content", response_class=FileResponse)
def read_attachment_content(attachment: AttachmentDependency, files_dir: FilesDirDependency) -> FileResponse:
    try:
        file = original_file(attachment_folder(files_dir, attachment))
    except AttachmentError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    inline = attachment.media_type in INLINE_MEDIA_TYPES
    return FileResponse(
        file,
        media_type=attachment.media_type if inline else "application/octet-stream",
        filename=f"{attachment.filename}.zip" if attachment.kind == "folder" else attachment.filename,
        content_disposition_type="inline" if inline else "attachment",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"},
    )


@router.delete("/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_attachment(attachment: AttachmentDependency, session: SessionDependency, files_dir: FilesDirDependency) -> None:
    try:
        delete_attachment(session, files_dir, attachment)
    except AttachmentError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
