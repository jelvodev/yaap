"""Memory consolidation.

The utility model reads the project memory and proposes merges of entries that say the same or overlap: each merge
names the entries it replaces and gives the merged text. Nothing changes until the user accepts a merge; an accepted
merge is one commit of the memory file, so it can be undone like any memory edit. Proposals are made on demand
(*Tidy up* in the Memory tab) and by themselves once TIDY_EVERY new entries came since the last tidy-up.
"""

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.blueprint.builtin import render, shipped_instructions
from app.core.background import BackgroundWork
from app.core.builtin_prompts import utility_instructions
from app.core.settings import resolve_languages
from app.core.utility import UtilityContext, run_utility
from app.projects.memory import (
    MAX_ENTRY_CHARS,
    Entry,
    MemoryChangedError,
    ProjectMemoryError,
    parse_entries,
    read_memory,
    short,
    write_memory,
)
from app.projects.service import project_path
from app.storage.models import Conversation, MemoryMerge, MemoryTidyState, Project

logger = logging.getLogger(__name__)

TIDY_EVERY = 20
MAX_MERGES = 10
# The entries go to the model as a numbered list; a memory is at most 64 KB, so this is a safety net only.
MAX_LIST_CHARS = 80_000
OPEN = "open"

# The answer format that parse_merges() reads; the instructions before it may be replaced.
TIDY_ANSWER = (
    "Answer with a JSON array only, no other text. Each item: "
    '{"entries": [the numbers of the entries it replaces],\n"text": "the merged entry"}. '
    "Answer [] when nothing should be merged."
)


def tidy_prompt(candidates: list[Entry], language: str, instructions: str | None = None) -> str:
    """What the utility model is asked: the instructions, which the user may replace, then the answer format."""
    template = instructions if instructions is not None else shipped_instructions("memory-keeper", "Tidy")
    lead = render(template, {"limit": str(MAX_MERGES), "language": language, "max_chars": str(MAX_ENTRY_CHARS)})
    return f"{lead}\n\n{TIDY_ANSWER}\n\n<entries>\n{numbered(candidates)}\n</entries>"


@dataclass(frozen=True)
class MergeProposal:
    # Indexes into the list of entries given to the model, in file order.
    indexes: tuple[int, ...]
    text: str


def parse_merges(answer: str, count: int) -> list[MergeProposal]:
    """The usable merges in the model's answer: two or more known entries each, no entry twice, a sensible text."""
    start, end = answer.find("["), answer.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        items = json.loads(answer[start : end + 1])
    except json.JSONDecodeError:
        return []
    used: set[int] = set()
    proposals: list[MergeProposal] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("entries"), list):
            continue
        numbers = []
        for value in item["entries"]:
            if isinstance(value, bool) or not isinstance(value, int | str) or not str(value).strip().isdigit():
                numbers = []
                break
            numbers.append(int(value))
        indexes = sorted({number - 1 for number in numbers})
        text = " ".join(str(item.get("text") or "").split())
        if len(indexes) < 2 or any(index < 0 or index >= count for index in indexes) or used & set(indexes):
            continue
        if len(text) < 15 or len(text) > MAX_ENTRY_CHARS:
            continue
        used.update(indexes)
        proposals.append(MergeProposal(tuple(indexes), text))
    return proposals[:MAX_MERGES]


def open_merges(session: Session, project_id: int) -> list[MemoryMerge]:
    return list(
        session.scalars(
            select(MemoryMerge).where(MemoryMerge.project_id == project_id, MemoryMerge.status == OPEN).order_by(MemoryMerge.id)
        )
    )


def match_entries(entries: list[Entry], wanted: list[dict[str, str]]) -> list[Entry] | None:
    """The current entries a merge replaces, matched by section and text; None when one of them is gone or changed."""
    found: list[Entry] = []
    for item in wanted:
        match = next(
            (
                entry
                for entry in entries
                if entry.text == item.get("text") and entry.section == item.get("section") and entry not in found
            ),
            None,
        )
        if match is None:
            return None
        found.append(match)
    return found


def refresh_open(session: Session, project_id: int, memory: str) -> list[MemoryMerge]:
    """The open merges that still fit the memory; the others are marked outdated."""
    entries = parse_entries(memory)
    current = []
    for merge in open_merges(session, project_id):
        if match_entries(entries, merge.entries) is None:
            merge.status = "outdated"
            merge.decided_at = datetime.now(UTC)
        else:
            current.append(merge)
    session.commit()
    return current


def numbered(entries: list[Entry]) -> str:
    lines = [f"{number}. [{entry.section or 'No section'}] {entry.text}" for number, entry in enumerate(entries, 1)]
    return "\n".join(lines)[:MAX_LIST_CHARS]


def record_check(session: Session, project_id: int, count: int) -> None:
    state = session.get(MemoryTidyState, project_id)
    if state is None:
        state = MemoryTidyState(project_id=project_id)
        session.add(state)
    state.entries_at_check = count
    state.checked_at = datetime.now(UTC)


async def tidy(context: UtilityContext, project_id: int, folder: Path) -> list[MemoryMerge] | None:
    """Ask the utility model for merges of the current memory; returns the new proposals, or None when the utility
    model is not available. Entries already in an open merge are left out, so proposals never overlap."""
    memory = read_memory(folder)
    entries = parse_entries(memory)
    with context.session_factory() as session:
        project = session.get_one(Project, project_id)
        environment_id = project.environment_id
        language = resolve_languages(session, environment_id, project_id).work
        waiting = refresh_open(session, project_id, memory)
        taken = [entry for merge in waiting for entry in (match_entries(entries, merge.entries) or [])]
        if len(entries) - len(taken) < 2:
            record_check(session, project_id, len(entries))
            session.commit()
            return []
    candidates = [entry for entry in entries if entry not in taken]
    instructions = utility_instructions(context, environment_id, "memory-keeper", "Tidy")
    prompt = tidy_prompt(candidates, language, instructions)
    answer = await run_utility(
        context, environment_id, prompt, None, max_tokens=4000, task="memory_tidy", project_id=project_id
    )
    if answer is None:
        return None
    created: list[MemoryMerge] = []
    with context.session_factory() as session:
        for proposal in parse_merges(answer, len(candidates)):
            replaced = [candidates[index] for index in proposal.indexes]
            merge = MemoryMerge(
                project_id=project_id,
                entries=[{"section": entry.section, "text": entry.text} for entry in replaced],
                section=replaced[0].section,
                text=proposal.text,
                status=OPEN,
                created_at=datetime.now(UTC),
            )
            session.add(merge)
            created.append(merge)
        record_check(session, project_id, len(entries))
        session.commit()
        for merge in created:
            session.refresh(merge)
            session.expunge(merge)
    return created


def due(session: Session, project_id: int, count: int) -> bool:
    """Whether TIDY_EVERY entries came since the last tidy-up. Entries removed meanwhile lower the mark, so it counts
    new entries rather than growth."""
    state = session.get(MemoryTidyState, project_id)
    if state is None:
        return count >= TIDY_EVERY
    if count < state.entries_at_check:
        state.entries_at_check = count
        session.commit()
    return count - state.entries_at_check >= TIDY_EVERY


# Projects with a tidy-up running, so a second trigger meanwhile does not start another.
RUNNING: set[int] = set()


async def tidy_if_due(context: UtilityContext, project_id: int, folder: Path) -> None:
    """A tidy-up when it is due; failures are logged, the user's work goes on."""
    if project_id in RUNNING:
        return
    count = len(parse_entries(read_memory(folder)))
    with context.session_factory() as session:
        if not due(session, project_id, count):
            return
    RUNNING.add(project_id)
    try:
        await tidy(context, project_id, folder)
    except Exception:
        logger.exception("Tidying up the project memory failed.")
    finally:
        RUNNING.discard(project_id)


def app_context(app: Any) -> UtilityContext:
    state = app.state
    return UtilityContext(
        state.session_factory, state.cipher, state.credential_types, state.providers, state.blueprint_dir
    )


# Background tidy checks, started after replies.
TIDY_CHECKS: BackgroundWork[None] = BackgroundWork()


def start_tidy_check(context: UtilityContext, project_id: int | None, folder: Path | None) -> None:
    """Check in the background whether a tidy-up is due (after a reply or a new entry)."""
    if project_id is None or folder is None:
        return
    TIDY_CHECKS.start(tidy_if_due(context, project_id, folder))


def start_tidy_after_reply(context: UtilityContext, workspace_dir: Path, conversation_id: int) -> None:
    """After a reply in a project conversation (the agent may have added entries with update_memory)."""
    with context.session_factory() as session:
        conversation = session.get(Conversation, conversation_id)
        project = session.get(Project, conversation.project_id) if conversation and conversation.project_id else None
        if project is None:
            return
        project_id, folder = project.id, project_path(workspace_dir, project)
    start_tidy_check(context, project_id, folder)


def merged_memory(memory: str, replaced: list[Entry], text: str) -> str:
    """The memory with the merged entry in the place of the first entry it replaces and the others removed."""
    cleaned = " ".join(text.split())
    if not cleaned or len(cleaned) > MAX_ENTRY_CHARS:
        raise ProjectMemoryError(f"An entry has 1 to {MAX_ENTRY_CHARS} characters.")
    lines = memory.replace("\r\n", "\n").split("\n")
    first = min(replaced, key=lambda entry: entry.start)
    for entry in sorted(replaced, key=lambda entry: entry.start, reverse=True):
        lines[entry.start : entry.end] = [f"- {cleaned}"] if entry is first else []
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines))


def accept_merge(folder: Path, environment_id: int, merge: MemoryMerge, text: str | None = None) -> None:
    """Write the merge (as edited) into the memory: one commit. Raises MemoryChangedError when an entry it replaces
    is gone or changed."""
    memory = read_memory(folder)
    replaced = match_entries(parse_entries(memory), merge.entries)
    if replaced is None:
        raise MemoryChangedError("An entry this merge replaces was changed or removed meanwhile; tidy up again.")
    final = text or merge.text
    write_memory(
        folder, environment_id, merged_memory(memory, replaced, final), f"Merge {len(replaced)} memory entries: {short(final)}"
    )
