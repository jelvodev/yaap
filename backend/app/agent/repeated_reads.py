"""A repeated read of the same text within one run costs one line instead of the text.

This holds within one run only. Across replies Claude Code may have compacted its history and YAAP's own loop may
have trimmed or summarised it, so a later reply reads in full. Nothing is left out that the model cannot get back:
the text is above in the same run.
"""

import hashlib


def text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def repeat_note(previous: tuple[str, int] | None, path: str, start: int, end: int, digest: str) -> str | None:
    """The one-line result for a read whose text equals the earlier read's; None when it must be shown."""
    if previous is None or previous[0] != digest:
        return None
    return f"Unchanged since read {previous[1]} of this reply ({path}, lines {start}–{end}); the text above is current."
