"""Project knowledge: reference documents of a project, searchable by the agent.

Documents live in YAAP's data folder, `knowledge/<project id>/<document id>/`, not in the repository: they can be
large or private and are not code. Each keeps its original file and `text.md`, the text extracted the way attachments
are read (PDF, Office, text, and the text files of a zip). The search index picks the text up as kind `knowledge`.
"""

import hashlib
import io
import re
import shutil
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.attachments import ocr
from app.attachments.files import AttachmentError, looks_like_text, store_upload
from app.storage.models import KnowledgeDocument, Project

MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_PROJECT_BYTES = 500 * 1024 * 1024
MAX_TEXT_CHARS = 2_000_000
# How much of a document the semantic index embeds; full-text search and read_knowledge see all of it.
MAX_INDEXED_CHARS = 400_000
TEXT_FILE = "text.md"


class KnowledgeError(Exception):
    """A document that cannot be added; the message is safe to show."""


def knowledge_dir(data_dir: Path) -> Path:
    return data_dir / "knowledge"


def document_folder(data_dir: Path, document: KnowledgeDocument) -> Path:
    return knowledge_dir(data_dir) / str(document.project_id) / str(document.id)


def read_text(data_dir: Path, document: KnowledgeDocument) -> str:
    path = document_folder(data_dir, document) / TEXT_FILE
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


def archive_text(folder: Path) -> str:
    parts: list[str] = []
    total = 0
    for file in sorted(path for path in folder.rglob("*") if path.is_file()):
        with file.open("rb") as handle:
            head = handle.read(4096)
        if not looks_like_text(head):
            continue
        text = file.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            continue
        part = f"## {file.relative_to(folder).as_posix()}\n\n{text}"
        parts.append(part)
        total += len(part)
        if total > MAX_TEXT_CHARS:
            break
    return "\n\n".join(parts)


def extracted_text(folder: Path, kind: str, original: Path) -> str:
    if kind in ("pdf", "office"):
        return (folder / "extracted.md").read_text(encoding="utf-8", errors="replace")
    if kind == "text":
        return original.read_text(encoding="utf-8", errors="replace")
    if kind == "archive":
        return archive_text(folder / "unpacked")
    if kind == "image":
        # A scan or photo of a page: its text, read with OCR.
        if not ocr.available():
            raise KnowledgeError("Reading text from images needs OCR, which this installation does not have.")
        text = ocr.read_image(original.read_bytes())
        return f"## {original.name} {ocr.OCR_NOTE}\n\n{text}" if text else ""
    raise KnowledgeError(
        "Only documents with text can be knowledge: PDF, Word, Excel, PowerPoint, text, a zip of them, or an image of text."
    )


def project_size(data_dir: Path, project_id: int) -> int:
    folder = knowledge_dir(data_dir) / str(project_id)
    return sum(path.stat().st_size for path in folder.rglob("*") if path.is_file()) if folder.is_dir() else 0


def add_document(session: Session, data_dir: Path, project: Project, filename: str, source: io.BufferedIOBase) -> KnowledgeDocument:
    if project_size(data_dir, project.id) > MAX_PROJECT_BYTES:
        raise KnowledgeError(f"This project's knowledge is over {MAX_PROJECT_BYTES // (1024 * 1024)} MB; remove documents first.")
    document = KnowledgeDocument(project_id=project.id, name=filename[:200] or "document", kind="text", size=0, chars=0)
    document.created_at = datetime.now(UTC)
    session.add(document)
    session.flush()
    folder = document_folder(data_dir, document)
    try:
        stored = store_upload(source, filename, folder)
        if stored.size > MAX_FILE_BYTES:
            raise KnowledgeError(f"{stored.filename} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB.")
        original = folder / "original" / stored.filename
        text = extracted_text(folder, stored.kind, original)[:MAX_TEXT_CHARS].strip()
        if not text:
            raise KnowledgeError(f"No text could be read from {stored.filename}.")
        (folder / TEXT_FILE).write_text(text, encoding="utf-8")
        # The unpacked copy of a zip is not needed once its text is kept.
        shutil.rmtree(folder / "unpacked", ignore_errors=True)
    except (AttachmentError, KnowledgeError, OSError) as error:
        session.rollback()
        shutil.rmtree(folder, ignore_errors=True)
        raise KnowledgeError(str(error)) from error
    document.name = stored.filename
    document.kind = stored.kind
    document.size = stored.size
    document.chars = len(text)
    session.commit()
    return document


# --- Web pages --------------------------------------------------------------------------------------


def fetch_page(url: str) -> tuple[str, str]:
    """The page's title and text, fetched as web_fetch does. Raises KnowledgeError."""
    from app.agent.web_tools import html_to_text

    if not re.match(r"^https?://", url):
        raise KnowledgeError("Only http and https pages can be added.")
    request = urllib.request.Request(url, headers={"User-Agent": "YAAP"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            content_type = response.headers.get("Content-Type", "")
            body = response.read(MAX_FILE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise KnowledgeError(f"Fetching {url} failed: {error}") from error
    if len(body) > MAX_FILE_BYTES:
        raise KnowledgeError(f"The page is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB.")
    text = body.decode("utf-8", errors="replace")
    title_match = re.search(r"<title[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
    title = " ".join(title_match.group(1).split())[:150] if title_match else url
    if "html" in content_type or text.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
        text = html_to_text(text)
    return title or url, text.strip()[:MAX_TEXT_CHARS]


def store_page(data_dir: Path, document: KnowledgeDocument, title: str, text: str) -> None:
    if not text:
        raise KnowledgeError("The page has no text.")
    folder = document_folder(data_dir, document)
    folder.mkdir(parents=True, exist_ok=True)
    full = f"# {title}\n\nSource: {document.source_url}\n\n{text}"
    (folder / TEXT_FILE).write_text(full, encoding="utf-8")
    document.name = title[:200]
    document.size = len(full.encode("utf-8"))
    document.chars = len(full)
    document.content_hash = hashlib.sha256(full.encode("utf-8")).hexdigest()
    document.fetched_at = datetime.now(UTC)


def add_page(session: Session, data_dir: Path, project: Project, url: str) -> KnowledgeDocument:
    title, text = fetch_page(url.strip())
    document = KnowledgeDocument(project_id=project.id, name=url[:200], kind="web", size=0, chars=0, source_url=url.strip())
    document.created_at = datetime.now(UTC)
    session.add(document)
    session.flush()
    try:
        store_page(data_dir, document, title, text)
    except KnowledgeError:
        session.rollback()
        raise
    session.commit()
    return document


def refresh_page(session: Session, data_dir: Path, document: KnowledgeDocument) -> bool:
    """Fetch the page again; True when its text changed."""
    if not document.source_url:
        raise KnowledgeError("Only a web page can be refreshed.")
    before = document.content_hash
    title, text = fetch_page(document.source_url)
    store_page(data_dir, document, title, text)
    session.commit()
    return document.content_hash != before


# --- A project's knowledge folder -------------------------------------------------------------------

FOLDER_SUFFIXES = {".md": "text", ".markdown": "text", ".txt": "text", ".rst": "text", ".adoc": "text", ".pdf": "pdf"}
MAX_FOLDER_FILES = 500


def folder_files(project_folder: Path, relative: str) -> dict[str, Path]:
    """The readable files under the knowledge folder, by their path in the project."""
    root = (project_folder / relative).resolve()
    if not root.is_dir() or not root.is_relative_to(project_folder.resolve()):
        return {}
    found: dict[str, Path] = {}
    for path in sorted(root.rglob("*")):
        if len(found) >= MAX_FOLDER_FILES:
            break
        if path.is_file() and path.suffix.lower() in FOLDER_SUFFIXES and ".git" not in path.parts:
            if path.stat().st_size <= MAX_FILE_BYTES:
                found[path.relative_to(project_folder.resolve()).as_posix()] = path
    return found


def folder_text(path: Path) -> str:
    if FOLDER_SUFFIXES[path.suffix.lower()] == "pdf":
        from app.attachments.files import pdf_text

        text, _pages = pdf_text(path, max_pages=2000)
        return text
    return path.read_text(encoding="utf-8", errors="replace")


def sync_folder(session: Session, data_dir: Path, project: Project, project_folder: Path, relative: str) -> int:
    """Mirror the knowledge folder: new and changed files are read, removed ones go. Returns how many changed."""
    files = folder_files(project_folder, relative) if relative else {}
    existing = {
        document.source_path: document
        for document in project_documents(session, project.id)
        if document.source_path is not None
    }
    changed = 0
    for path_text, document in existing.items():
        if path_text not in files:
            remove_document(session, data_dir, document)
            changed += 1
    for path_text, path in files.items():
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        document = existing.get(path_text)
        if document is not None and document.content_hash == digest:
            continue
        try:
            text = folder_text(path)[:MAX_TEXT_CHARS].strip()
        except (AttachmentError, OSError):
            text = ""
        if not text:
            continue
        if document is None:
            document = KnowledgeDocument(
                project_id=project.id,
                name=path_text[:200],
                kind=FOLDER_SUFFIXES[path.suffix.lower()],
                size=0,
                chars=0,
                source_path=path_text,
            )
            document.created_at = datetime.now(UTC)
            session.add(document)
            session.flush()
        folder = document_folder(data_dir, document)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / TEXT_FILE).write_text(text, encoding="utf-8")
        document.size, document.chars, document.content_hash = len(data), len(text), digest
        document.fetched_at = datetime.now(UTC)
        session.commit()
        changed += 1
    return changed


def sync_knowledge_folder(session: Session, data_dir: Path, project: Project, project_folder: Path) -> int:
    """Mirror the project's knowledge folder setting; errors are logged, so an index pass goes on."""
    import logging

    from app.core.settings import SETTING_DEFINITIONS, resolve_setting

    relative = str(resolve_setting(session, SETTING_DEFINITIONS["knowledge_folder"], project.environment_id, project.id).value or "")
    try:
        return sync_folder(session, data_dir, project, project_folder, relative.strip().strip("/"))
    except Exception:  # noqa: BLE001 - one project's folder must not stop the index of the others
        session.rollback()
        logging.getLogger(__name__).exception("Reading the knowledge folder of project %s failed.", project.id)
        return 0


def remove_document(session: Session, data_dir: Path, document: KnowledgeDocument) -> None:
    folder = document_folder(data_dir, document)
    session.delete(document)
    session.commit()
    shutil.rmtree(folder, ignore_errors=True)


def project_documents(session: Session, project_id: int) -> list[KnowledgeDocument]:
    return list(
        session.scalars(select(KnowledgeDocument).where(KnowledgeDocument.project_id == project_id).order_by(KnowledgeDocument.name))
    )


def find_document(session: Session, project_id: int, name: str) -> KnowledgeDocument | None:
    """By exact name, else by a name that starts with or contains it (case-insensitive) when that is unique."""
    documents = project_documents(session, project_id)
    for document in documents:
        if document.name == name:
            return document
    lowered = name.lower()
    matches = [document for document in documents if lowered in document.name.lower()]
    return matches[0] if len(matches) == 1 else None


def document_count(session: Session, project_id: int) -> int:
    return session.scalar(select(func.count(KnowledgeDocument.id)).where(KnowledgeDocument.project_id == project_id)) or 0


def remove_project_knowledge(data_dir: Path, project_id: int) -> None:
    shutil.rmtree(knowledge_dir(data_dir) / str(project_id), ignore_errors=True)
