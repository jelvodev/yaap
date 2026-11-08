"""Attachments in conversations: creating and removing them, and turning them into model input."""

import base64
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.attachments.files import (
    UNPACKED_KINDS,
    AttachmentError,
    archive_entries,
    image_for_model,
    original_file,
    remove_folder,
    store_upload,
)
from app.providers.base import ContentBlock
from app.storage.models import Attachment, Conversation, Environment

# What one attachment may add to a message as text; the rest is read with the attachment tools.
INLINE_TEXT_LIMIT = 60_000
# Without tools, an archive brings its small text files along up to this budget.
ARCHIVE_TEXT_BUDGET = 100_000
ARCHIVE_FILE_LIMIT = 20_000


def attachment_folder(files_dir: Path, attachment: Attachment) -> Path:
    root = files_dir.resolve()
    folder = (root / attachment.folder).resolve()
    if not folder.is_relative_to(root):
        raise AttachmentError("The attachment folder is outside the files folder.")
    return folder


def create_attachment(
    session: Session,
    files_dir: Path,
    conversation: Conversation,
    filename: str,
    source: BinaryIO,
    as_folder: bool = False,
) -> Attachment:
    environment = session.get_one(Environment, conversation.environment_id)
    relative_folder = f"{environment.slug}/{secrets.token_hex(12)}"
    folder = files_dir / relative_folder
    folder.mkdir(parents=True)
    try:
        stored = store_upload(source, filename, folder, as_folder)
    except Exception:
        remove_folder(folder)
        raise
    attachment = Attachment(
        environment_id=conversation.environment_id,
        conversation_id=conversation.id,
        filename=stored.filename,
        media_type=stored.media_type,
        kind=stored.kind,
        size=stored.size,
        folder=relative_folder,
        details=stored.details,
    )
    session.add(attachment)
    session.commit()
    return attachment


def delete_attachment(session: Session, files_dir: Path, attachment: Attachment) -> None:
    if attachment.message_id is not None:
        raise AttachmentError("This attachment was sent with a message and stays part of the conversation.")
    remove_folder(attachment_folder(files_dir, attachment))
    session.delete(attachment)
    session.commit()


def claim_attachments(session: Session, conversation: Conversation, attachment_ids: list[int]) -> list[Attachment]:
    """The unsent attachments of this conversation that go with the next message, in the order given."""
    if not attachment_ids:
        return []
    found = {
        attachment.id: attachment
        for attachment in session.scalars(
            select(Attachment).where(
                Attachment.id.in_(attachment_ids),
                Attachment.conversation_id == conversation.id,
                Attachment.message_id.is_(None),
            )
        )
    }
    missing = [str(identifier) for identifier in attachment_ids if identifier not in found]
    if missing:
        raise AttachmentError(f"Unknown or already sent attachment: {', '.join(missing)}.")
    return [found[identifier] for identifier in attachment_ids]


def attachment_block(attachment: Attachment) -> ContentBlock:
    """How an attachment is recorded in the stored message."""
    return {
        "type": "attachment",
        "attachment_id": attachment.id,
        "name": attachment.filename,
        "kind": attachment.kind,
        "media_type": attachment.media_type,
        "size": attachment.size,
    }


def conversation_attachments(session: Session, conversation_id: int) -> list[Attachment]:
    return list(
        session.scalars(
            select(Attachment)
            .where(Attachment.conversation_id == conversation_id, Attachment.message_id.is_not(None))
            .order_by(Attachment.id)
        )
    )


# --- Model input ---------------------------------------------------------------------------------------------------


def label(attachment: Attachment) -> str:
    return f'<attachment id="{attachment.id}" name="{attachment.filename}" kind="{attachment.kind}">'


def limited(text: str, limit: int, with_tools: bool, attachment: Attachment) -> str:
    if len(text) <= limit:
        return text
    hint = (
        f'Read the rest with read_attachment (attachment "{attachment.id}").'
        if with_tools
        else "The rest is not included."
    )
    return text[:limit] + f"\n\n[Shortened: {len(text) - limit} more characters. {hint}]"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def archive_text(attachment: Attachment, folder: Path, with_tools: bool) -> str:
    overview = str(attachment.details.get("overview") or "")
    if with_tools:
        return (
            f"{overview}\n\nUse list_attachment, read_attachment and search_attachment with attachment "
            f'"{attachment.id}" to look inside.'
        )
    # No tools to look inside: bring the small text files along within a budget.
    from app.attachments.files import looks_like_text

    unpacked = folder / "unpacked"
    parts = [overview]
    budget = ARCHIVE_TEXT_BUDGET
    left_out = 0
    for entry in archive_entries(unpacked):
        file = unpacked / entry.path
        if entry.size > ARCHIVE_FILE_LIMIT or entry.size == 0:
            continue
        with file.open("rb") as handle:
            if not looks_like_text(handle.read(4096)):
                continue
        text = read_text(file)
        if len(text) > budget:
            left_out += 1
            continue
        budget -= len(text)
        parts.append(f"--- {entry.path} ---\n{text}")
    if left_out:
        parts.append(f"[{left_out} more text files left out to keep the message small.]")
    return "\n\n".join(parts)


def model_blocks(attachment: Attachment, files_dir: Path, with_tools: bool) -> list[ContentBlock]:
    """The neutral content blocks that carry one attachment to the model."""
    folder = attachment_folder(files_dir, attachment)
    opening = label(attachment)
    closing = "</attachment>"
    if attachment.kind == "image":
        data, media_type = image_for_model(folder, attachment.media_type)
        return [
            {"type": "text", "text": f"{opening}{closing}"},
            {"type": "image", "media_type": media_type, "data": base64.b64encode(data).decode()},
        ]
    if attachment.kind == "pdf" and not attachment.details.get("as_text"):
        return [
            {"type": "text", "text": f"{opening}{closing}"},
            {
                "type": "document",
                "media_type": "application/pdf",
                "data": base64.b64encode(original_file(folder).read_bytes()).decode(),
                "title": attachment.filename,
            },
        ]
    if attachment.kind in ("pdf", "office"):
        text = limited(read_text(folder / "extracted.md"), INLINE_TEXT_LIMIT, with_tools, attachment)
    elif attachment.kind == "text":
        text = limited(read_text(original_file(folder)), INLINE_TEXT_LIMIT, with_tools, attachment)
    elif attachment.kind in UNPACKED_KINDS:
        text = archive_text(attachment, folder, with_tools)
    else:
        text = f"A binary file of {attachment.size} bytes ({attachment.media_type}); its content cannot be read as text."
    return [{"type": "text", "text": f"{opening}\n{text}\n{closing}"}]


@dataclass
class AttachmentResolver:
    """Replaces stored attachment blocks with model input when a request is built."""

    session: Session
    files_dir: Path
    with_tools: bool

    def expand(self, content: list[ContentBlock]) -> list[ContentBlock]:
        if not any(block.get("type") == "attachment" for block in content):
            return content
        expanded: list[ContentBlock] = []
        for block in content:
            if block.get("type") != "attachment":
                expanded.append(block)
                continue
            attachment = self.session.get(Attachment, block.get("attachment_id"))
            if attachment is None:
                expanded.append({"type": "text", "text": f"[Attachment {block.get('name')} is no longer available.]"})
                continue
            try:
                expanded += model_blocks(attachment, self.files_dir, self.with_tools)
            except (AttachmentError, OSError) as error:
                expanded.append({"type": "text", "text": f"[Attachment {attachment.filename} cannot be read: {error}]"})
        return expanded


def attachment_summary(attachment: Attachment) -> dict[str, Any]:
    details = attachment.details or {}
    return {
        key: details[key]
        for key in (
            "width", "height", "pages", "file_count", "unpacked_size", "extracted_chars", "as_text", "note", "left_out"
        )
        if key in details
    }
