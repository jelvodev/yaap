"""Project memory: .blueprint/memory.md in the project's repository.

Decisions, conventions and lessons learned that the agent should know in every conversation of the project. The
agent adds entries with the update_memory tool; the user reads and edits the file on the project panel. Every change
is a commit of only this file, so it is visible and reversible in the project's history.
"""

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.blueprint.layers import PROJECT_LAYER_FOLDER, BlueprintError, Layer, commit

MEMORY_FILE = "memory.md"
SECTIONS = ("Decisions", "Conventions", "Lessons learned", "Notes")
TEMPLATE = "# Project memory\n\n" + "".join(f"## {section}\n\n" for section in SECTIONS)
MAX_MEMORY_BYTES = 64 * 1024
MAX_ENTRY_CHARS = 1000


class ProjectMemoryError(Exception):
    """The memory cannot be changed this way; the message says why."""


class MemoryChangedError(ProjectMemoryError):
    """The entry to change is no longer where it was: the memory changed in the meantime."""


def memory_layer(project_folder: Path, label: str = "project") -> Layer:
    return Layer("project", label, project_folder / PROJECT_LAYER_FOLDER, project_folder, f"{PROJECT_LAYER_FOLDER}/")


def memory_path(project_folder: Path) -> Path:
    return project_folder / PROJECT_LAYER_FOLDER / MEMORY_FILE


def read_memory(project_folder: Path) -> str:
    path = memory_path(project_folder)
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def has_entries(text: str) -> bool:
    """Whether the memory says anything beyond its headings."""
    return any(line.strip() and not line.lstrip().startswith("#") for line in text.splitlines())


def write_memory(project_folder: Path, content: str, message: str) -> None:
    normalized = content.replace("\r\n", "\n")
    if len(normalized.encode()) > MAX_MEMORY_BYTES:
        raise ProjectMemoryError(
            "The project memory is larger than 64 KB. Keep it to what every conversation needs."
        )
    path = memory_path(project_folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized if normalized.endswith("\n") else normalized + "\n", encoding="utf-8")
    try:
        commit(memory_layer(project_folder), MEMORY_FILE, message)
    except BlueprintError as error:
        raise ProjectMemoryError(str(error)) from error


def add_entry(text: str, section: str, entry: str) -> str:
    """The memory with one bullet added at the end of a section; the section is created when it is missing."""
    bullet = "- " + " ".join(entry.split())
    lines = (text or TEMPLATE).rstrip("\n").split("\n")
    heading = re.compile(rf"^##\s+{re.escape(section)}\s*$", re.IGNORECASE)
    start = next((index for index, line in enumerate(lines) if heading.match(line)), None)
    if start is None:
        return "\n".join(lines) + f"\n\n## {section}\n\n{bullet}\n"
    end = next((index for index in range(start + 1, len(lines)) if lines[index].startswith("## ")), len(lines))
    insert_at = end
    while insert_at > start + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    block = [bullet] if insert_at > start + 1 else ["", bullet]
    trailing = [""] if end < len(lines) else []
    return "\n".join(lines[:insert_at] + block + trailing + lines[end:]) + "\n"


def remember(project_folder: Path, section: str, entry: str) -> str:
    if section not in SECTIONS:
        raise ProjectMemoryError(f"Section must be one of: {', '.join(SECTIONS)}.")
    if not entry.strip() or len(entry) > MAX_ENTRY_CHARS:
        raise ProjectMemoryError(f"An entry has 1 to {MAX_ENTRY_CHARS} characters.")
    updated = add_entry(read_memory(project_folder), section, entry)
    summary = " ".join(entry.split())
    write_memory(project_folder, updated, f"Remember: {summary[:60]}{'…' if len(summary) > 60 else ''}")
    return updated


# --- Entries: the dashboard edits one entry at a time --------------------------------------------------


@dataclass(frozen=True)
class Entry:
    """One remembered item: a bullet (with its indented continuation lines) or a loose line under a section."""

    section: str
    text: str
    # Lines start..end (exclusive) of the file, counted from 0.
    start: int
    end: int

    @property
    def id(self) -> str:
        """Its place and a hash of its text, so an edit of a memory that changed meanwhile is refused."""
        return f"{self.start}-{hashlib.sha1(self.text.encode()).hexdigest()[:8]}"


BULLET = re.compile(r"^\s*[-*+]\s+")


def parse_entries(text: str) -> list[Entry]:
    entries: list[Entry] = []
    section = ""
    lines = text.replace("\r\n", "\n").split("\n")
    current: tuple[int, list[str]] | None = None

    def close(end: int) -> None:
        nonlocal current
        if current is not None:
            start, parts = current
            entries.append(Entry(section, " ".join(" ".join(parts).split()), start, end))
            current = None

    for number, line in enumerate(lines):
        stripped = line.strip()
        if line.startswith("#"):
            close(number)
            if line.startswith("## "):
                section = line[3:].strip()
            continue
        if not stripped:
            close(number)
            continue
        if BULLET.match(line):
            close(number)
            current = (number, [BULLET.sub("", line, count=1)])
        elif current is not None and line[:1].isspace():
            current[1].append(stripped)
        else:
            close(number)
            current = (number, [stripped])
    close(len(lines))
    return [entry for entry in entries if entry.text]


def find_entry(text: str, entry_id: str) -> Entry:
    for entry in parse_entries(text):
        if entry.id == entry_id:
            return entry
    raise MemoryChangedError("The memory changed in the meantime; reload it and try again.")


def replace_entry(text: str, entry_id: str, new_text: str | None) -> tuple[str, Entry]:
    """The memory with one entry changed, or removed when new_text is None."""
    entry = find_entry(text, entry_id)
    lines = text.replace("\r\n", "\n").split("\n")
    if new_text is None:
        replacement: list[str] = []
    else:
        cleaned = " ".join(new_text.split())
        if not cleaned or len(cleaned) > MAX_ENTRY_CHARS:
            raise ProjectMemoryError(f"An entry has 1 to {MAX_ENTRY_CHARS} characters.")
        replacement = [f"- {cleaned}"]
    return "\n".join(lines[: entry.start] + replacement + lines[entry.end :]), entry


def edit_entry(project_folder: Path, entry_id: str, new_text: str) -> str:
    updated, entry = replace_entry(read_memory(project_folder), entry_id, new_text)
    write_memory(project_folder, updated, f"Edit memory: {short(new_text)}")
    return updated


def delete_entry(project_folder: Path, entry_id: str) -> str:
    updated, entry = replace_entry(read_memory(project_folder), entry_id, None)
    write_memory(project_folder, updated, f"Forget: {short(entry.text)}")
    return updated


def short(text: str) -> str:
    text = " ".join(text.split())
    return text[:60] + ("…" if len(text) > 60 else "")


# --- Recall: what of a large memory goes into the prompt ----------------------------------------------

# Below this (estimated) size the whole memory goes into the prompt.
RECALL_TOKENS = 4000
RECENT_PER_SECTION = 3
# Room for the entries closest to the user's message, on top of the recent ones.
RELATED_TOKENS = 2500
MAX_RELATED = 20
WORD = re.compile(r"\w{3,}", re.UNICODE)


def estimate_tokens(text: str) -> int:
    return (len(text) + 3) // 4


@dataclass
class Recall:
    """The memory as it goes into the prompt, and which entries were chosen and why."""

    text: str
    whole: bool
    total: int
    recent: list[Entry] = field(default_factory=list)
    related: list[Entry] = field(default_factory=list)


def word_overlap(entry: Entry, words: set[str]) -> float:
    entry_words = {word.lower() for word in WORD.findall(entry.text)}
    return len(entry_words & words) / (len(words) or 1)


def recall(text: str, query: str, related_chunks: list[str] | None = None) -> Recall:
    """The whole memory while it is small; above RECALL_TOKENS its headings, the newest entries per section (the last
    ones, as entries are added at the end) and the entries closest to the user's message.

    related_chunks are the memory's search chunks closest in meaning to the message, best first; entries in them count
    as related. Without them, or after them, entries sharing words with the message count.
    """
    entries = parse_entries(text)
    if not has_entries(text) or estimate_tokens(text) <= RECALL_TOKENS:
        return Recall(text, True, len(entries))
    by_section: dict[str, list[Entry]] = {}
    for entry in entries:
        by_section.setdefault(entry.section, []).append(entry)
    recent = [entry for items in by_section.values() for entry in items[-RECENT_PER_SECTION:]]
    chosen = {entry.start for entry in recent}

    related: list[Entry] = []
    budget = RELATED_TOKENS

    def take(entry: Entry) -> None:
        nonlocal budget
        cost = estimate_tokens(entry.text) + 2
        if entry.start in chosen or cost > budget or len(related) >= MAX_RELATED:
            return
        related.append(entry)
        chosen.add(entry.start)
        budget -= cost

    for chunk in related_chunks or []:
        normalized = " ".join(chunk.split())
        for entry in entries:
            if entry.text and entry.text in normalized:
                take(entry)
    words = {word.lower() for word in WORD.findall(query)}
    # Words most entries share ("the", the project's name) say nothing about which entry matters.
    frequency: dict[str, int] = {}
    for entry in entries:
        for word in {word.lower() for word in WORD.findall(entry.text)} & words:
            frequency[word] = frequency.get(word, 0) + 1
    words = {word for word in words if frequency.get(word, 0) <= max(3, len(entries) // 5)}
    if words:
        scored = sorted(((word_overlap(entry, words), entry) for entry in entries), key=lambda pair: -pair[0])
        for score, entry in scored:
            if score <= 0:
                break
            take(entry)

    sections = []
    for section, items in by_section.items():
        shown = [entry for entry in items if entry.start in chosen]
        heading = f"## {section}" if section else ""
        body = "\n".join(f"- {entry.text}" for entry in shown)
        omitted = len(items) - len(shown)
        note = f"({omitted} more {'entry' if omitted == 1 else 'entries'} not shown)" if omitted else ""
        sections.append("\n\n".join(part for part in (heading, body, note) if part))
    header = (
        f"The memory has {len(entries)} entries, more than fits here. Shown: the newest entries of each section and "
        "those closest to the user's message. Search the rest with search_project (include: memory)."
    )
    return Recall("\n\n".join([header, *sections]), False, len(entries), recent, related)
