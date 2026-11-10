"""Which GitHub repository the update check reads. Pure."""

import re
from typing import Any

DEFAULT_SOURCE = "jelvodev/yaap"
SOURCE_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


def source_error(value: Any) -> str | None:
    if isinstance(value, str) and SOURCE_PATTERN.fullmatch(value.strip()):
        return None
    return "Use a GitHub repository such as jelvodev/yaap."
