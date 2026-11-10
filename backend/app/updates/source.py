"""Which GitHub repository the update check reads. Pure."""

import re
from typing import Any

DEFAULT_SOURCE = "jelvodev/yaap"
SOURCE_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


def is_source(value: str) -> bool:
    """Whether the text is `owner/repository`; `.` and `..` are no names."""
    return SOURCE_PATTERN.fullmatch(value) is not None and not any(part in (".", "..") for part in value.split("/"))


def source_error(value: Any) -> str | None:
    if isinstance(value, str) and is_source(value.strip()):
        return None
    return "Use a GitHub repository such as jelvodev/yaap."
