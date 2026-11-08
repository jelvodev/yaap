"""Opening and saving a project file in the editor.

Saving uses the agent's hash check: the editor sends the hash of the version it opened, and a save is refused when
the file changed since, so the user and the agent never overwrite each other's unseen changes. The agent's own check
(`refuse_if_changed`) then refuses its next edit of a file the user saved after the agent read it. In YAAP's container
a checkpoint is taken first, so a save can be compared and rolled back like an action of the agent.
"""

import asyncio
from dataclasses import dataclass

from app.agent.guard import content_hash
from app.attachments.files import classify
from app.checkpoints.service import CheckpointStore
from app.files.service import ProjectFiles, name_of
from app.storage.models import Project
from app.targets.base import FileEntry, TargetError, TargetFiles

# Text up to this size can be edited; larger text opens read-only; beyond the view limit it is not opened.
MAX_EDIT_BYTES = 1024 * 1024
MAX_VIEW_BYTES = 10 * 1024 * 1024
# Images and PDFs are sent to the browser up to this size.
MAX_RAW_BYTES = 25 * 1024 * 1024
# Only these are shown inline; SVG is text, so it never runs as a page of YAAP.
INLINE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif", "application/pdf"}


class FileChanged(Exception):
    """The file changed since the editor opened it."""


@dataclass
class OpenedFile:
    entry: FileEntry
    # text, image, pdf, binary or too_large
    kind: str
    media_type: str
    hash: str | None
    text: str | None
    read_only: bool
    # Why the file opens read-only or not at all, for the user.
    note: str | None


async def open_file(files: TargetFiles, path: str) -> OpenedFile:
    """A file as the editor shows it: text with its hash, or an image or PDF to show, or why it does not open."""
    relative = files.check(path)
    entry = await files.stat(relative)
    if entry is None or entry.is_dir:
        raise TargetError(f"{path} does not exist or is not a file.")
    if entry.size > MAX_VIEW_BYTES:
        note = f"The file is larger than {MAX_VIEW_BYTES // (1024 * 1024)} MB, too large to open here."
        return OpenedFile(entry, "too_large", "application/octet-stream", None, None, True, note)
    data = await files.read_bytes(relative)
    digest = content_hash(data)
    kind, media_type = classify(name_of(relative), data[:4096])
    if kind in ("image", "pdf") and media_type in INLINE_TYPES:
        return OpenedFile(entry, kind, media_type, digest, None, True, None)
    if kind != "text":
        return OpenedFile(entry, "binary", media_type, digest, None, True, "A binary file; it cannot be shown as text.")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
        return OpenedFile(entry, "text", media_type, digest, text, True, "Not valid UTF-8, so it opens read-only.")
    if entry.size > MAX_EDIT_BYTES:
        note = f"Larger than {MAX_EDIT_BYTES // (1024 * 1024)} MB, so it opens read-only."
        return OpenedFile(entry, "text", media_type, digest, text, True, note)
    try:
        files.check(relative, for_write=True)
    except TargetError:
        return OpenedFile(entry, "text", media_type, digest, text, True, "Files inside .git open read-only.")
    return OpenedFile(entry, "text", media_type, digest, text, False, None)


async def raw_file(files: TargetFiles, path: str) -> tuple[bytes, str, str]:
    """The bytes of a file to show in the browser, its media type and name."""
    relative = files.check(path)
    entry = await files.stat(relative)
    if entry is None or entry.is_dir:
        raise TargetError(f"{path} does not exist or is not a file.")
    if entry.size > MAX_RAW_BYTES:
        raise TargetError(f"The file is larger than {MAX_RAW_BYTES // (1024 * 1024)} MB.")
    data = await files.read_bytes(relative)
    _kind, media_type = classify(name_of(relative), data[:4096])
    return data, media_type, name_of(relative)


@dataclass
class SavedFile:
    entry: FileEntry
    hash: str
    checkpoint_id: int | None


async def save_file(
    located: ProjectFiles,
    store: CheckpointStore,
    project: Project,
    path: str,
    text: str,
    base_hash: str | None,
) -> SavedFile:
    """Save text over the version the editor opened (base_hash), after a checkpoint; FileChanged when it changed.

    base_hash None creates a new file and is refused when the file exists by then.
    """
    files = located.files
    relative = files.check(path, for_write=True)
    found = await files.stat(relative)
    if found is not None and found.is_dir:
        raise TargetError(f"{path} is a folder.")
    current = None if found is None else content_hash(await files.read_bytes(relative))
    if current != base_hash:
        if current is None:
            raise FileChanged(f"{relative} was removed after you opened it.")
        if base_hash is None:
            raise FileChanged(f"{relative} exists already.")
        raise FileChanged(f"{relative} changed after you opened it. Reload it to see the other changes first.")
    checkpoint_id = None
    if located.folder is not None:
        checkpoint_id = await asyncio.to_thread(
            store.take, project.id, located.folder, "editor", f"Saved {relative} in the editor", kind="edit"
        )
    data = text.encode("utf-8")
    await files.write_bytes(relative, data)
    entry = await files.stat(relative)
    assert entry is not None
    return SavedFile(entry, content_hash(data), checkpoint_id)
