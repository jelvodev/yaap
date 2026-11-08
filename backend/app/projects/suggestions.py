"""Memory suggestions.

The utility model reads a project conversation since it was last checked and proposes at most MAX_SUGGESTIONS entries
for the project memory. Nothing is written without the user: each suggestion waits in the Memory tab to be accepted
(one commit, as any memory edit), edited or dismissed. A suggestion close to an existing entry is offered as an update
of that entry. Suggestions are made after a goal ends, after every TURNS_BETWEEN new turns, and on demand. When they
are due after the first reply of a conversation, its title comes in the same call (`suggest_with_title`).
"""

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.search_index import message_text
from app.core.settings import resolve_languages
from app.core.utility import UtilityContext, clean_title, run_utility, title_request
from app.projects.memory import (
    MAX_ENTRY_CHARS,
    SECTIONS,
    WORD,
    Entry,
    ProjectMemoryError,
    edit_entry,
    parse_entries,
    read_memory,
    remember,
)
from app.storage.models import Conversation, Goal, MemorySuggestion, Message

logger = logging.getLogger(__name__)

MAX_SUGGESTIONS = 5
TURNS_BETWEEN = 10
MAX_CONVERSATION_CHARS = 40_000
# Sharing this share of words with an existing entry makes a suggestion an update of that entry.
SIMILAR_WORDS = 0.6
OPEN = "open"
# What is only true for this session: temporary paths, local ports, a report of what the agent did.
TEMPORARY = re.compile(
    r"(/tmp/|\\temp\\|localhost:\d+|127\.0\.0\.1:\d+|^(the agent|i|we|yaap) (ran|run|tried|executed|started)\b)",
    re.IGNORECASE,
)

PROMPT = """You keep the memory of a software project: short entries that later conversations about this project
should know. Read the conversation below and propose at most {limit} new entries.

Good entries are lasting: decisions and why, conventions of the codebase, lessons learned from mistakes, facts about
the project's setup. Leave out what only mattered for this conversation: the task's steps, temporary files, ports,
what the agent tried, and anything the memory already says.

{answer}

<memory>
{memory}
</memory>

<conversation>
{conversation}
</conversation>"""

ENTRY_SHAPE = """{{"section": one of {sections}, "text": "the entry"}}. Write each entry in {language} as one or two
sentences, whatever language the conversation is in."""
ANSWER_ENTRIES = (
    "Answer with a JSON array only, no other text. Each item: " + ENTRY_SHAPE + " Answer [] when there is nothing worth "
    "keeping."
)
# The first suggestions of a new conversation come with its title, in one call.
ANSWER_WITH_TITLE = (
    "This is a new conversation, so also write {title}. Answer with a JSON object only, no other text: "
    '{{"title": "the title", "entries": [the entries]}}. Each entry: ' + ENTRY_SHAPE + ' Use "entries": [] when '
    "there is nothing worth keeping."
)


@dataclass(frozen=True)
class Proposal:
    section: str
    text: str


def words_of(text: str) -> set[str]:
    return {word.lower() for word in WORD.findall(text)}


def similar_entry(entries: list[Entry], text: str) -> Entry | None:
    """The existing entry that says nearly the same, by shared words."""
    words = words_of(text)
    if not words:
        return None
    best, best_score = None, 0.0
    for entry in entries:
        other = words_of(entry.text)
        if not other:
            continue
        score = len(words & other) / min(len(words), len(other))
        if score > best_score:
            best, best_score = entry, score
    return best if best_score >= SIMILAR_WORDS else None


def parse_proposals(answer: str) -> list[Proposal]:
    start, end = answer.find("["), answer.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        items = json.loads(answer[start : end + 1])
    except json.JSONDecodeError:
        return []
    return proposals_from(items)


def parse_title_and_proposals(answer: str) -> tuple[str | None, list[Proposal]]:
    """The title and the entries of a combined answer (ANSWER_WITH_TITLE)."""
    start, end = answer.find("{"), answer.rfind("}")
    if start == -1 or end <= start:
        return None, []
    try:
        reply = json.loads(answer[start : end + 1])
    except json.JSONDecodeError:
        return None, []
    if not isinstance(reply, dict):
        return None, []
    title = clean_title(reply["title"]) if isinstance(reply.get("title"), str) else None
    return title, proposals_from(reply.get("entries"))


def proposals_from(items: object) -> list[Proposal]:
    proposals = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text") or "").split())
        section = str(item.get("section") or "Notes")
        if section not in SECTIONS:
            section = "Notes"
        if len(text) < 15 or len(text) > MAX_ENTRY_CHARS or TEMPORARY.search(text):
            continue
        proposals.append(Proposal(section, text))
    return proposals[:MAX_SUGGESTIONS]


def conversation_text(session: Session, conversation_id: int, since: datetime | None) -> str:
    query = select(Message).where(Message.conversation_id == conversation_id, Message.status == "complete")
    if since is not None:
        query = query.where(Message.created_at > since)
    lines = []
    for message in session.scalars(query.order_by(Message.id)):
        text = message_text(message)
        if text:
            lines.append(f"{'User' if message.role == 'user' else 'Assistant'}: {text}")
    text = "\n\n".join(lines)
    return text[-MAX_CONVERSATION_CHARS:]


def open_suggestions(session: Session, project_id: int) -> list[MemorySuggestion]:
    return list(
        session.scalars(
            select(MemorySuggestion)
            .where(MemorySuggestion.project_id == project_id, MemorySuggestion.status == OPEN)
            .order_by(MemorySuggestion.id)
        )
    )


@dataclass(frozen=True)
class SuggestionInput:
    """What a suggestion call reads: the conversation since the last check and the memory with what waits."""

    project_id: int
    environment_id: int
    conversation: str
    memory: str
    pending: list[str]
    language: str


def suggestion_input(context: UtilityContext, conversation_id: int, project_folder: Path) -> SuggestionInput | None:
    """None when there is nothing to read: no project, or nothing new since the last check."""
    with context.session_factory() as session:
        conversation = session.get_one(Conversation, conversation_id)
        project_id, environment_id = conversation.project_id, conversation.environment_id
        text = conversation_text(session, conversation_id, conversation.memory_checked_at)
        pending = [item.text for item in open_suggestions(session, project_id)] if project_id else []
        language = resolve_languages(session, environment_id, project_id).work
    if project_id is None or not text:
        return None
    return SuggestionInput(project_id, environment_id, text, read_memory(project_folder), pending, language)


def suggestion_prompt(found: SuggestionInput, answer: str, reply_language: str = "") -> str:
    """The prompt with the answer it asks for: ANSWER_ENTRIES, or ANSWER_WITH_TITLE with the title in reply_language."""
    known = found.memory + "".join(f"\n- {item} (suggested, waiting)" for item in found.pending)
    sections = ", ".join(SECTIONS)
    title = title_request(reply_language)
    return PROMPT.format(
        limit=MAX_SUGGESTIONS,
        answer=answer.format(sections=sections, language=found.language, title=title),
        memory=known.strip() or "(empty)",
        conversation=found.conversation,
    )


async def suggest(context: UtilityContext, conversation_id: int, project_folder: Path) -> list[MemorySuggestion]:
    """Ask the utility model for entries from the conversation since it was last checked; returns the new ones."""
    found = suggestion_input(context, conversation_id, project_folder)
    if found is None:
        return []
    prompt = suggestion_prompt(found, ANSWER_ENTRIES)
    answer = await run_utility(
        context, found.environment_id, prompt, conversation_id, max_tokens=1500, task="memory_suggestion"
    )
    if answer is None:
        return []
    return store_proposals(context, conversation_id, found, parse_proposals(answer))


async def suggest_with_title(
    context: UtilityContext, conversation_id: int, project_folder: Path, reply_language: str = ""
) -> str | None:
    """The first suggestions of a new conversation and its title, in one utility call; returns the title. The
    suggestions wait in the Memory tab as those of suggest() do, and the conversation counts as checked."""
    found = suggestion_input(context, conversation_id, project_folder)
    if found is None:
        return None
    prompt = suggestion_prompt(found, ANSWER_WITH_TITLE, reply_language)
    answer = await run_utility(
        context, found.environment_id, prompt, conversation_id, max_tokens=1600, task="title_and_memory"
    )
    if answer is None:
        return None
    title, proposals = parse_title_and_proposals(answer)
    store_proposals(context, conversation_id, found, proposals)
    return title


def store_proposals(
    context: UtilityContext, conversation_id: int, found: SuggestionInput, proposals: list[Proposal]
) -> list[MemorySuggestion]:
    """Keep the new proposals as open suggestions and mark the conversation as checked; returns the new ones."""
    project_id, pending = found.project_id, found.pending
    entries = parse_entries(found.memory)
    created = []
    with context.session_factory() as session:
        for proposal in proposals:
            if proposal.text in pending or any(entry.text == proposal.text for entry in entries):
                continue
            match = similar_entry(entries, proposal.text)
            item = MemorySuggestion(
                project_id=project_id,
                conversation_id=conversation_id,
                section=match.section if match else proposal.section,
                text=proposal.text,
                updates_entry=match.text if match else None,
                status=OPEN,
                created_at=datetime.now(UTC),
            )
            session.add(item)
            created.append(item)
        session.get_one(Conversation, conversation_id).memory_checked_at = datetime.now(UTC)
        session.commit()
        for item in created:
            session.refresh(item)
            session.expunge(item)
    return created


def due(session: Session, conversation_id: int) -> bool:
    """Whether suggestions are due: a goal ended, or TURNS_BETWEEN user messages came, since the last check."""
    conversation = session.get(Conversation, conversation_id)
    if conversation is None or conversation.project_id is None:
        return False
    since = conversation.memory_checked_at
    turns = select(func.count()).select_from(Message).where(Message.conversation_id == conversation_id, Message.role == "user")
    goals = select(func.count()).select_from(Goal).where(
        Goal.conversation_id == conversation_id, Goal.status.in_(("done", "blocked", "limit")), Goal.finished_at.is_not(None)
    )
    if since is not None:
        turns = turns.where(Message.created_at > since)
        goals = goals.where(Goal.finished_at > since)
    return (session.scalar(turns) or 0) >= TURNS_BETWEEN or (session.scalar(goals) or 0) > 0


def accept(project_folder: Path, suggestion: MemorySuggestion, section: str | None, text: str | None) -> None:
    """Write the suggestion (as edited) into the memory: one commit. An update replaces the entry it matched, when
    that entry is still there; otherwise the text is added."""
    accept_entry(project_folder, section or suggestion.section, text or suggestion.text, suggestion.updates_entry)


def accept_entry(project_folder: Path, section: str, text: str, updates_entry: str | None) -> None:
    """Write an entry into the memory, replacing updates_entry when it is still there: one commit. Shared with the
    memory lines of learning proposals (app/learning)."""
    final = " ".join(text.split())
    if not final:
        raise ProjectMemoryError("The entry is empty.")
    if updates_entry:
        current = next((entry for entry in parse_entries(read_memory(project_folder)) if entry.text == updates_entry), None)
        if current is not None:
            edit_entry(project_folder, current.id, final)
            return
    remember(project_folder, section, final)
