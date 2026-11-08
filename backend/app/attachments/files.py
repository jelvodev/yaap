"""Attachment files on disk: storing, recognising, unpacking and reading them.

Layout of one attachment under /data/files/<environment>/<key>/:
- original/<filename>   the file as uploaded
- model.<ext>           an image scaled down for the model, when the original is too large
- extracted.md          text taken from an Office document or PDF
- unpacked/             the contents of a zip archive, or of a folder (sent by the browser as a zip)
"""

import io
import os
import re
import shutil
import stat
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree

MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_UNPACKED_BYTES = 500 * 1024 * 1024
MAX_ARCHIVE_FILES = 5000
MAX_PDF_BYTES = 32 * 1024 * 1024
MAX_PDF_PAGES = 100
# Images are scaled so the longest side fits and the file stays well under the model's 5 MB limit.
MAX_IMAGE_SIDE = 2000
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_TEXT_BYTES = 2 * 1024 * 1024

IMAGE_TYPES = {"image/png": "png", "image/jpeg": "jpeg", "image/webp": "webp", "image/gif": "gif"}
OFFICE_EXTENSIONS = {".docx", ".xlsx", ".pptx"}
ARCHIVE_EXTENSIONS = {".zip"}
NESTED_ARCHIVE_EXTENSIONS = {".zip", ".tar", ".gz", ".tgz", ".7z", ".rar", ".bz2", ".xz"}
SKIPPED_ARCHIVE_ENTRIES = ("__MACOSX/", ".DS_Store", "Thumbs.db")
# A folder attachment leaves these out, like the agent's file tools (SKIPPED_FOLDERS in app/agent/tools.py). The
# browser already leaves them out; this keeps them out of an upload that did not.
SKIPPED_FOLDER_NAMES = frozenset({".git", "node_modules", ".venv", "__pycache__", "dist", "build", ".next"})
# Kinds whose files are unpacked under unpacked/ and read one by one with the attachment tools.
UNPACKED_KINDS = ("archive", "folder")
TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".conf", ".env.example", ".xml", ".html", ".htm", ".css", ".scss", ".svg", ".log", ".sql", ".py", ".ts", ".tsx",
    ".js", ".jsx", ".mjs", ".cjs", ".java", ".kt", ".cs", ".go", ".rs", ".rb", ".php", ".sh", ".ps1", ".psm1", ".bat",
    ".c", ".h", ".cpp", ".hpp", ".swift", ".dart", ".vue", ".svelte", ".tf", ".dockerfile", ".gitignore", ".lock",
}  # fmt: skip


class AttachmentError(Exception):
    """The file cannot be used as an attachment; the message says why."""


# --- Recognising ---------------------------------------------------------------------------------------------------


def image_type_of(head: bytes) -> str | None:
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    return None


def looks_like_text(head: bytes) -> bool:
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as error:
        # A multi-byte character cut off at the end of the sample is still text.
        return error.start >= len(head) - 3
    return True


def classify(filename: str, head: bytes) -> tuple[str, str]:
    """The kind and media type of a file, judged by its content first and its name second."""
    suffix = Path(filename).suffix.lower()
    image = image_type_of(head)
    if image:
        return "image", image
    if head.startswith(b"%PDF"):
        return "pdf", "application/pdf"
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        if suffix in OFFICE_EXTENSIONS:
            office_types = {
                ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            }
            return "office", office_types[suffix]
        return "archive", "application/zip"
    if looks_like_text(head):
        media_type = "image/svg+xml" if suffix == ".svg" else "text/plain"
        return "text", media_type
    return "other", "application/octet-stream"


def safe_filename(name: str) -> str:
    base = PurePosixPath(name.replace("\\", "/")).name
    base = re.sub(r"[^\w.\- ()]+", "_", base).strip(" .")
    return base[:200] or "file"


# --- Storing -------------------------------------------------------------------------------------------------------


@dataclass
class StoredFile:
    filename: str
    kind: str
    media_type: str
    size: int
    details: dict[str, Any] = field(default_factory=dict)


def original_file(folder: Path) -> Path:
    files = [path for path in (folder / "original").iterdir() if path.is_file()]
    if not files:
        raise AttachmentError("The attachment's file is missing.")
    return files[0]


def folder_name(filename: str) -> str:
    """The name of a folder attachment: the folder's own name, without the .zip the browser sent it in."""
    name = safe_filename(filename)
    return (name[:-4] or "folder") if name.lower().endswith(".zip") else name


def store_upload(source: io.BufferedIOBase, filename: str, folder: Path, as_folder: bool = False) -> StoredFile:
    """Copy the upload into the attachment folder within the size limit, then recognise and prepare it.

    With as_folder the upload is a folder the browser packed as a zip: it is kept as one attachment of kind folder,
    named after the folder, with its paths.
    """
    name = folder_name(filename) if as_folder else safe_filename(filename)
    target = folder / "original" / (f"{name}.zip" if as_folder else name)
    target.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    with target.open("wb") as output:
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                raise AttachmentError(f"{name} is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
            output.write(chunk)
    if size == 0:
        raise AttachmentError(f"{name} is empty.")
    with target.open("rb") as file:
        head = file.read(4096)
    kind, media_type = classify(name, head)
    if as_folder:
        if kind != "archive":
            raise AttachmentError(f"The folder {name} did not arrive as a zip file.")
        kind = "folder"
    stored = StoredFile(name, kind, media_type, size)
    prepare(stored, target, folder)
    return stored


def prepare(stored: StoredFile, original: Path, folder: Path) -> None:
    if stored.kind == "image":
        stored.details.update(prepare_image(original, folder))
    elif stored.kind == "pdf":
        stored.details.update(prepare_pdf(original, folder))
    elif stored.kind == "office":
        text = extract_office_text(original)
        (folder / "extracted.md").write_text(text, encoding="utf-8")
        stored.details["extracted_chars"] = len(text)
    elif stored.kind == "archive":
        stored.details.update(unpack_archive(original, folder / "unpacked"))
    elif stored.kind == "folder":
        unpacked = unpack_archive(
            original, folder / "unpacked", skip_folders=SKIPPED_FOLDER_NAMES, label="Folder", name=stored.filename
        )
        stored.details.update(unpacked)
    elif stored.kind == "text" and stored.size > MAX_TEXT_BYTES:
        stored.details["note"] = "Large text file: the model reads it in parts."


def remove_folder(folder: Path) -> None:
    shutil.rmtree(folder, ignore_errors=True)


# --- Images --------------------------------------------------------------------------------------------------------


def scaled_image(data: bytes) -> tuple[bytes, str] | None:
    """A smaller copy for the model when the image is too large; None when the original can be sent as it is."""
    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        image.load()
        if max(image.size) <= MAX_IMAGE_SIDE and len(data) <= MAX_IMAGE_BYTES:
            return None
        frame = image.convert("RGBA" if image.mode in ("RGBA", "LA", "P") else "RGB")
        frame.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
        output = io.BytesIO()
        frame.save(output, format="PNG", optimize=True)
        if output.tell() <= MAX_IMAGE_BYTES:
            return output.getvalue(), "image/png"
        output = io.BytesIO()
        frame.convert("RGB").save(output, format="JPEG", quality=85)
        return output.getvalue(), "image/jpeg"


def prepare_image(original: Path, folder: Path) -> dict[str, Any]:
    from PIL import Image, UnidentifiedImageError

    try:
        data = original.read_bytes()
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
        scaled = scaled_image(data)
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise AttachmentError(f"{original.name} is not an image that can be read: {error}") from error
    details: dict[str, Any] = {"width": width, "height": height}
    if scaled:
        content, media_type = scaled
        model_file = folder / f"model.{IMAGE_TYPES[media_type]}"
        model_file.write_bytes(content)
        details["scaled_for_model"] = model_file.name
    return details


def image_for_model(folder: Path, media_type: str) -> tuple[bytes, str]:
    for name, scaled_type in (("model.png", "image/png"), ("model.jpeg", "image/jpeg")):
        if (folder / name).exists():
            return (folder / name).read_bytes(), scaled_type
    return original_file(folder).read_bytes(), media_type


from app.attachments.ocr import OCR_NOTE, read_scanned_pages  # noqa: E402


# --- PDF -----------------------------------------------------------------------------------------------------------


def pdf_text(source: Path | bytes, max_pages: int = MAX_PDF_PAGES) -> tuple[str, int]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(source) if isinstance(source, bytes) else source)
        pages = len(reader.pages)
        texts = [(page.extract_text() or "").strip() for page in reader.pages[:max_pages]]
    except (PdfReadError, ValueError, KeyError, OSError) as error:
        raise AttachmentError(f"The PDF cannot be read: {error}") from error
    # Scanned pages have no text of their own: OCR reads them when Tesseract is there.
    try:
        scanned = read_scanned_pages(source, texts)
    except Exception:  # noqa: BLE001 - OCR is extra; the text layer still counts
        scanned = {}
    parts = [
        f"## Page {number} {OCR_NOTE}\n\n{scanned[number]}" if number in scanned else f"## Page {number}\n\n{text}"
        for number, text in enumerate(texts, 1)
    ]
    return "\n\n".join(parts), pages


def prepare_pdf(original: Path, folder: Path) -> dict[str, Any]:
    text, pages = pdf_text(original)
    (folder / "extracted.md").write_text(text, encoding="utf-8")
    details: dict[str, Any] = {"pages": pages, "extracted_chars": len(text)}
    if original.stat().st_size > MAX_PDF_BYTES or pages > MAX_PDF_PAGES:
        # Too large to send as a document: the model gets the extracted text instead.
        details["as_text"] = True
    return details


# --- Office --------------------------------------------------------------------------------------------------------

WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
DRAWING = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
MAX_SHEET_ROWS = 2000


def docx_text(archive: zipfile.ZipFile) -> str:
    root = ElementTree.fromstring(archive.read("word/document.xml"))
    paragraphs = []
    for paragraph in root.iter(f"{WORD}p"):
        style = paragraph.find(f"{WORD}pPr/{WORD}pStyle")
        text = "".join(node.text or "" for node in paragraph.iter(f"{WORD}t"))
        if not text.strip():
            continue
        level = re.match(r"Heading(\d)", style.get(f"{WORD}val", "")) if style is not None else None
        paragraphs.append(f"{'#' * int(level.group(1))} {text}" if level else text)
    return "\n\n".join(paragraphs)


def pptx_text(archive: zipfile.ZipFile) -> str:
    def slide_number(name: str) -> int:
        match = re.search(r"(\d+)\.xml$", name)
        return int(match.group(1)) if match else 0

    slides = sorted(
        (name for name in archive.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", name)), key=slide_number
    )
    parts = []
    for name in slides:
        root = ElementTree.fromstring(archive.read(name))
        lines = []
        for paragraph in root.iter(f"{DRAWING}p"):
            text = "".join(node.text or "" for node in paragraph.iter(f"{DRAWING}t")).strip()
            if text:
                lines.append(text)
        parts.append(f"## Slide {slide_number(name)}\n\n" + "\n".join(lines))
    return "\n\n".join(parts)


def markdown_cell(value: Any) -> str:
    return "" if value is None else str(value).replace("|", "\\|").replace("\n", " ")


def xlsx_text(path: Path) -> str:
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=True)
    parts = []
    try:
        for sheet in workbook.worksheets:
            rows = []
            for index, row in enumerate(sheet.iter_rows(values_only=True)):
                if index >= MAX_SHEET_ROWS:
                    rows.append(None)
                    break
                rows.append([markdown_cell(value) for value in row])
            filled = [row for row in rows if row is None or any(row)]
            if not filled:
                parts.append(f"## Sheet: {sheet.title}\n\n(empty)")
                continue
            width = max(len(row) for row in filled if row is not None)
            lines = []
            for number, row in enumerate(filled):
                if row is None:
                    lines.append(f"\n[Only the first {MAX_SHEET_ROWS} rows are shown.]")
                    break
                cells = row + [""] * (width - len(row))
                lines.append("| " + " | ".join(cells) + " |")
                if number == 0:
                    lines.append("|" + " --- |" * width)
            parts.append(f"## Sheet: {sheet.title}\n\n" + "\n".join(lines))
    finally:
        workbook.close()
    return "\n\n".join(parts)


def extract_office_text(path: Path) -> str:
    suffix = path.suffix.lower()
    try:
        if suffix == ".xlsx":
            return xlsx_text(path)
        with zipfile.ZipFile(path) as archive:
            return docx_text(archive) if suffix == ".docx" else pptx_text(archive)
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError, ValueError, OSError) as error:
        raise AttachmentError(f"{path.name} cannot be read as an Office document: {error}") from error


# --- Archives ------------------------------------------------------------------------------------------------------


def safe_member_path(name: str) -> PurePosixPath | None:
    """The path inside the archive when it stays inside the target folder; None for anything else."""
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or re.match(r"^[A-Za-z]:", normalized) or ".." in path.parts or not path.parts:
        return None
    return path


def unpack_archive(
    original: Path,
    target: Path,
    skip_folders: frozenset[str] = frozenset(),
    label: str = "Archive",
    name: str | None = None,
) -> dict[str, Any]:
    """Unpack a zip safely: no paths outside the folder, no symlinks, and limits on size and number of files.

    Entries inside a folder named in skip_folders (.git, node_modules) are left out and counted.
    """
    skipped: list[str] = []
    left_out = 0
    nested: list[str] = []
    files = 0
    written = 0
    try:
        archive = zipfile.ZipFile(original)
    except zipfile.BadZipFile as error:
        raise AttachmentError(f"{original.name} is not a valid zip file.") from error
    target.mkdir(parents=True, exist_ok=True)
    with archive:
        for member in archive.infolist():
            if member.is_dir() or any(part in member.filename for part in SKIPPED_ARCHIVE_ENTRIES):
                continue
            path = safe_member_path(member.filename)
            mode = member.external_attr >> 16
            if path is None or stat.S_ISLNK(mode):
                skipped.append(member.filename)
                continue
            if skip_folders and any(part in skip_folders for part in path.parts[:-1]):
                left_out += 1
                continue
            if member.flag_bits & 0x1:
                raise AttachmentError(f"{original.name} is password-protected; YAAP cannot unpack it.")
            files += 1
            if files > MAX_ARCHIVE_FILES:
                raise AttachmentError(f"{original.name} holds more than {MAX_ARCHIVE_FILES} files.")
            destination = target.joinpath(*path.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, destination.open("wb") as output:
                # Count what is actually written, not what the archive claims, so a zip bomb stops early.
                while chunk := source.read(1024 * 1024):
                    written += len(chunk)
                    if written > MAX_UNPACKED_BYTES:
                        raise AttachmentError(
                            f"{original.name} unpacks to more than {MAX_UNPACKED_BYTES // (1024 * 1024)} MB."
                        )
                    output.write(chunk)
            if path.suffix.lower() in NESTED_ARCHIVE_EXTENSIONS:
                nested.append(path.as_posix())
    entries = archive_entries(target)
    details: dict[str, Any] = {
        "file_count": files,
        "unpacked_size": written,
        "skipped": skipped[:50],
        "nested_archives": nested[:50],
        "overview": archive_overview(name or original.name, target, entries, skipped, nested, label, left_out),
    }
    if left_out:
        details["left_out"] = left_out
    return details


@dataclass(frozen=True)
class ArchiveEntry:
    path: str
    size: int


def archive_entries(root: Path) -> list[ArchiveEntry]:
    entries = []
    for current, folders, names in os.walk(root):
        folders.sort()
        for name in sorted(names):
            file = Path(current) / name
            entries.append(ArchiveEntry(file.relative_to(root).as_posix(), file.stat().st_size))
    return entries


PROJECT_MARKERS = [
    ("package.json", "a JavaScript or TypeScript project"),
    ("pyproject.toml", "a Python project"),
    ("requirements.txt", "a Python project"),
    ("setup.py", "a Python project"),
    ("go.mod", "a Go project"),
    ("Cargo.toml", "a Rust project"),
    ("pom.xml", "a Java project"),
    ("build.gradle", "a Java or Kotlin project"),
    ("composer.json", "a PHP project"),
    ("Gemfile", "a Ruby project"),
    ("Dockerfile", "a containerised application"),
]
CONTENT_GROUPS = {
    "images": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".heic"},
    "spreadsheets and data files": {".xlsx", ".xls", ".csv", ".tsv", ".json"},
    "documents": {".pdf", ".docx", ".doc", ".pptx", ".odt", ".rtf"},
    "Markdown documents": {".md", ".markdown"},
    "source code": {".py", ".ts", ".tsx", ".js", ".jsx", ".cs", ".java", ".go", ".rs", ".php", ".rb", ".ps1", ".sh"},
}
TREE_LIMIT = 200
README_EXCERPT = 1500


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def describe_archive(entries: list[ArchiveEntry]) -> str:
    names = {PurePosixPath(entry.path).name for entry in entries}
    projects = sorted({label for marker, label in PROJECT_MARKERS if marker in names})
    suffixes = Counter(PurePosixPath(entry.path).suffix.lower() for entry in entries)
    groups = Counter()
    for group, members in CONTENT_GROUPS.items():
        groups[group] = sum(count for suffix, count in suffixes.items() if suffix in members)
    findings = []
    if projects:
        findings.append("It looks like " + " and ".join(dict.fromkeys(projects)) + ".")
    main_group, main_count = groups.most_common(1)[0] if groups else ("", 0)
    if main_count and main_count >= 0.6 * len(entries):
        findings.append(f"Most files are {main_group} ({main_count} of {len(entries)}).")
    elif groups:
        mixed = ", ".join(f"{count} {group}" for group, count in groups.most_common() if count)
        if mixed:
            findings.append(f"It contains {mixed}.")
    return " ".join(findings) or "No clear project or content type recognised."


def tree_lines(entries: list[ArchiveEntry]) -> list[str]:
    if len(entries) <= TREE_LIMIT:
        return [f"{entry.path} ({human_size(entry.size)})" for entry in entries]
    # Too many files: one line per folder with its file count and size.
    folders: dict[str, list[ArchiveEntry]] = {}
    for entry in entries:
        parent = PurePosixPath(entry.path).parent.as_posix()
        folders.setdefault(parent, []).append(entry)
    lines = [
        f"{folder}/ ({len(items)} files, {human_size(sum(item.size for item in items))})"
        for folder, items in sorted(folders.items())
    ]
    if len(lines) > TREE_LIMIT:
        lines = lines[:TREE_LIMIT] + [f"[{len(lines) - TREE_LIMIT} more folders not shown.]"]
    return lines


def archive_overview(
    name: str,
    root: Path,
    entries: list[ArchiveEntry],
    skipped: list[str],
    nested: list[str],
    label: str = "Archive",
    left_out: int = 0,
) -> str:
    total = sum(entry.size for entry in entries)
    size = f"{human_size(total)} unpacked" if label == "Archive" else human_size(total)
    lines = [
        f"{label} {name}: {len(entries)} files, {size}.",
        describe_archive(entries),
        "",
        "Files:",
        *tree_lines(entries),
    ]
    readme = next(
        (entry for entry in entries if PurePosixPath(entry.path).name.lower() in ("readme.md", "readme.txt", "readme")),
        None,
    )
    if readme is not None:
        text = (root / readme.path).read_text(encoding="utf-8", errors="replace")[:README_EXCERPT]
        lines += ["", f"Start of {readme.path}:", text.strip()]
    if nested:
        lines += ["", "Archives inside this archive (not unpacked): " + ", ".join(nested[:20])]
    if skipped:
        lines += ["", f"Skipped {len(skipped)} entries with unsafe paths or symbolic links."]
    if left_out:
        lines += ["", f"Left out {left_out} files in .git and dependency folders."]
    return "\n".join(lines)
