"""The Current state block of the project memory: where the work stands, what is open and the next step.

It is the first section of `.blueprint/memory.md`, kept short (MAX_STATE_CHARS), and rewritten as a whole rather than
added to entry by entry. Every conversation of the project reads it on every turn. The functions here are pure: they
read and replace the block in the memory's text; writing and committing the file is `projects/memory.py`'s.
"""

import re
from pathlib import Path

STATE_SECTION = "Current state"
MAX_STATE_CHARS = 1500
LEAD_INS = ("Where it stands", "Open", "Next step")
STATE_HEADING = re.compile(rf"^##\s+{re.escape(STATE_SECTION)}\s*$", re.IGNORECASE)
# A heading inside the block would end it, or start a new document, when the file is read back.
BLOCK_BREAKING_LINE = re.compile(r"^\s{0,3}#{1,2}\s")
# Commits of the block start with this, so its last change can be found in the file's history.
COMMIT_PREFIX = "Current state: "
TEMPLATE_TITLE = "# Project memory"


def is_state_section(section: str) -> bool:
    return section.strip().lower() == STATE_SECTION.lower()


def has_repository(project_folder: Path) -> bool:
    """Whether the project folder is a git repository, which every change of the memory is committed in."""
    return (project_folder / ".git").exists()


def state_lines(lines: list[str]) -> tuple[int, int] | None:
    """The block's heading line and the end (exclusive) of its body, or None when there is no block."""
    start = next((index for index, line in enumerate(lines) if STATE_HEADING.match(line)), None)
    if start is None:
        return None
    end = next((index for index in range(start + 1, len(lines)) if lines[index].startswith("## ")), len(lines))
    return start, end


def read_state(text: str) -> str | None:
    """The block's text without its heading, or None when the memory has no block. An empty block is ""."""
    lines = text.replace("\r\n", "\n").split("\n")
    found = state_lines(lines)
    if found is None:
        return None
    start, end = found
    return "\n".join(lines[start + 1 : end]).strip()


def without_state(text: str) -> str:
    """The memory without the block, for where the block is shown on its own."""
    lines = text.replace("\r\n", "\n").split("\n")
    found = state_lines(lines)
    if found is None:
        return text
    start, end = found
    return "\n".join(lines[:start] + lines[end:])


def replace_state(text: str, new: str) -> str:
    """The memory with the block's text replaced by new; a missing block is added before the other sections. Keeps
    the file's line endings, CRLF or LF. Replacing with the same text again gives the same file."""
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.replace("\r\n", "\n").rstrip("\n").split("\n") if text.strip() else [TEMPLATE_TITLE]
    block = [f"## {STATE_SECTION}", "", *new.replace("\r\n", "\n").strip().split("\n"), ""]
    found = state_lines(lines)
    if found is not None:
        start, end = found
        rest = lines[end:]
        updated = lines[:start] + block + rest if rest else lines[:start] + block[:-1]
    else:
        first_section = next((index for index, line in enumerate(lines) if line.startswith("## ")), None)
        if first_section is None:
            head = lines
            while head and not head[-1].strip():
                head = head[:-1]
            updated = [*head, "", *block[:-1]]
        else:
            head = lines[:first_section]
            while head and not head[-1].strip():
                head = head[:-1]
            updated = [*head, "", *block, *lines[first_section:]]
    return newline.join(updated) + newline


def check_state(new: str) -> str | None:
    """Why the text cannot be the block, or None when it can."""
    stripped = new.strip()
    if not stripped:
        return "The current state is empty."
    if len(stripped) > MAX_STATE_CHARS:
        return f"The current state has at most {MAX_STATE_CHARS} characters; this has {len(stripped)}."
    if any(BLOCK_BREAKING_LINE.match(line) for line in stripped.replace("\r\n", "\n").split("\n")):
        return "The current state cannot contain a heading line (starting with # or ##); use bold lead-ins instead."
    return None
