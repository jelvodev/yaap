"""OCR: text from scanned PDF pages and images, with Tesseract (Dutch and English) when it is installed.

A PDF page counts as scanned when it has (almost) no text of its own; its largest image is read. At most
MAX_OCR_PAGES pages of a document are read this way.
"""

import io
import logging
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from app.core.subprocess_env import safe_subprocess_env

if TYPE_CHECKING:
    from pypdf import PageObject

logger = logging.getLogger(__name__)

LANGUAGES = "nld+eng"
MAX_OCR_PAGES = 100
SCANNED_BELOW_CHARS = 20
PAGE_TIMEOUT = 60
OCR_NOTE = "(read with OCR)"


def available() -> bool:
    return shutil.which("tesseract") is not None


def as_png(data: bytes) -> bytes:
    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        frame = image.convert("L") if image.mode not in ("L", "RGB") else image
        output = io.BytesIO()
        frame.save(output, "PNG")
        return output.getvalue()


def read_image(data: bytes) -> str:
    """The text in an image; empty when Tesseract is missing or reads nothing."""
    command = shutil.which("tesseract")
    if command is None:
        return ""
    try:
        png = as_png(data)
    except Exception:  # noqa: BLE001 - an image Pillow cannot open has no text for us
        return ""
    try:
        result = subprocess.run(
            [command, "stdin", "stdout", "-l", LANGUAGES],
            input=png,
            capture_output=True,
            timeout=PAGE_TIMEOUT,
            check=False,
            env=safe_subprocess_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        logger.warning("OCR failed: %s", error)
        return ""
    return result.stdout.decode("utf-8", errors="replace").strip()


def page_image(page: "PageObject") -> bytes | None:
    """The largest image on a PDF page."""
    try:
        images = list(page.images)
    except Exception:  # noqa: BLE001 - pypdf raises many kinds for odd images
        return None
    best = max(images, key=lambda image: len(image.data), default=None)
    return best.data if best is not None else None


def read_scanned_pages(source: Path | bytes, texts: list[str]) -> dict[int, str]:
    """OCR text per page number (1-based) for the pages of `texts` that have no text of their own."""
    if not available():
        return {}
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(source) if isinstance(source, bytes) else source)
    found: dict[int, str] = {}
    done = 0
    for index, text in enumerate(texts):
        if len(text.strip()) >= SCANNED_BELOW_CHARS or done >= MAX_OCR_PAGES:
            continue
        data = page_image(reader.pages[index])
        if data is None:
            continue
        done += 1
        read = read_image(data)
        if read:
            found[index + 1] = read
    return found
