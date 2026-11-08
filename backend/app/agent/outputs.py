"""Full tool output.

A tool output longer than STORE_ABOVE is kept whole in `data/outputs/<conversation>/<n>.txt`; the model gets its start
and end with the output's number and reads the rest with `read_output`. The chat offers the full output too. Outputs
go with their conversation and after KEEP_DAYS; they are not part of backups.
"""

import re
import shutil
import threading
import time
from pathlib import Path

# Output between a few thousand characters and this stays whole in the history, where repeated test runs add up.
STORE_ABOVE = 6_000
# What the model gets of a stored output; together well below STORE_ABOVE, so storing always shortens.
HEAD_CHARS = 2_000
TAIL_CHARS = 3_000
KEEP_DAYS = 30
MAX_READ_LINES = 400
MAX_READ_CHARS = 10_000
MAX_MATCHES = 200
# Tools that page through their own content, or whose text must arrive whole (a skill), are never shortened here.
WHOLE_TOOLS = frozenset(
    {"read_file", "read_knowledge", "read_attachment", "read_output", "load_skill", "load_standard", "read_process_output"}
)
NOTE = re.compile(r"\[Full output: output (\d+),")
_lock = threading.Lock()


def outputs_dir(data_dir: Path) -> Path:
    return data_dir / "outputs"


class OutputStore:
    def __init__(self, data_dir: Path, conversation_id: int) -> None:
        self.folder = outputs_dir(data_dir) / str(conversation_id)

    def save(self, text: str) -> int:
        with _lock:
            self.folder.mkdir(parents=True, exist_ok=True)
            numbers = [int(path.stem) for path in self.folder.glob("*.txt") if path.stem.isdigit()]
            number = max(numbers, default=0) + 1
            (self.folder / f"{number}.txt").write_bytes(text.encode("utf-8"))
        return number

    def path(self, number: int) -> Path | None:
        path = self.folder / f"{int(number)}.txt"
        return path if path.is_file() else None

    def read(self, number: int) -> str | None:
        path = self.path(number)
        return path.read_bytes().decode("utf-8", errors="replace") if path else None


def shortened(text: str, number: int) -> str:
    lines = text.count("\n") + 1
    left_out = len(text) - HEAD_CHARS - TAIL_CHARS
    return (
        f"{text[:HEAD_CHARS]}\n\n[… {left_out:,} characters left out …]\n\n{text[-TAIL_CHARS:]}\n\n"
        f"[Full output: output {number}, {lines:,} lines, {len(text):,} characters. Read any part with read_output "
        "(a range of lines, or a search).]"
    )


def read_range(text: str, start_line: int, end_line: int | None) -> str:
    lines = text.split("\n")
    start = max(start_line, 1)
    end = min(end_line or start + MAX_READ_LINES - 1, start + MAX_READ_LINES - 1, len(lines))
    chosen = [f"{number}: {lines[number - 1]}" for number in range(start, end + 1)]
    body = "\n".join(chosen)
    if len(body) > MAX_READ_CHARS:
        body = body[:MAX_READ_CHARS] + "\n[Cut at 10,000 characters; ask for fewer lines.]"
    more = f"\n[Lines {start}-{end} of {len(lines)}.]" if end < len(lines) or start > 1 else ""
    return body + more


def search_lines(text: str, pattern: str) -> str:
    try:
        expression = re.compile(pattern, re.IGNORECASE)
    except re.error:
        expression = re.compile(re.escape(pattern), re.IGNORECASE)
    matches = [f"{number}: {line[:300]}" for number, line in enumerate(text.split("\n"), 1) if expression.search(line)]
    if not matches:
        return f"No line matches {pattern!r}."
    shown = matches[:MAX_MATCHES]
    extra = f"\n[{len(matches) - MAX_MATCHES} more matches.]" if len(matches) > MAX_MATCHES else ""
    return "\n".join(shown) + extra


def remove_conversation_outputs(data_dir: Path, conversation_id: int) -> None:
    shutil.rmtree(outputs_dir(data_dir) / str(conversation_id), ignore_errors=True)


def prune_outputs(data_dir: Path, days: int = KEEP_DAYS) -> int:
    """Remove outputs older than `days`; returns how many files went."""
    folder = outputs_dir(data_dir)
    if not folder.is_dir():
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    for path in folder.glob("*/*.txt"):
        if path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)
            removed += 1
    for conversation in folder.iterdir():
        if conversation.is_dir() and not any(conversation.iterdir()):
            conversation.rmdir()
    return removed
