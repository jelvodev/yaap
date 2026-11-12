"""Writing the Current state block, and the safety net that rewrites it when the agent did not.

The agent keeps the block itself with update_current_state. When a goal or a build ends, or a long conversation goes
quiet, without the agent having written it, the utility task `current_state` (the built-in skill state-keeper)
rewrites it from what happened. The answer is checked like any block (`check_state`) and written as one commit of the
memory file; an invalid answer keeps the old block, and a change of the block since it was read (by the user or the
agent) wins over the refresh.
"""

import logging
import re
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import anyio
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.blueprint.builtin import render
from app.blueprint.layers import PROJECT_LAYER_FOLDER
from app.core.builtin_prompts import utility_instructions
from app.core.goals import last_ended_goal
from app.core.settings import resolve_languages
from app.core.utility import UtilityContext, run_utility
from app.git.common import GitError
from app.git.place import git_place
from app.git.service import run_git
from app.projects.current_state import (
    COMMIT_PREFIX,
    MAX_STATE_CHARS,
    STATE_SECTION,
    check_state,
    has_repository,
    read_state,
    replace_state,
)
from app.projects.memory import MEMORY_FILE, ProjectMemoryError, read_memory, write_memory
from app.projects.service import ProjectError, project_path
from app.projects.suggestions import conversation_text
from app.storage.models import Conversation, Message, Project

logger = logging.getLogger(__name__)

QUIET_MESSAGES = 8
QUIET_MINUTES = 10
# A goal that ended longer ago than this is not refreshed for any more: a later reply found it, not its own end.
GOAL_END_WINDOW = timedelta(minutes=30)
MAX_ANSWER_TOKENS = 800
StateErrorKind = Literal["no_repository", "state_invalid"]
FENCE = re.compile(r"^```[\w-]*\n(.*)\n```$", re.DOTALL)


class StateError(ProjectMemoryError):
    """The block cannot be written; kind says why, the message is safe to show."""

    def __init__(self, kind: StateErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def refresh_due(goal_or_build_ended: bool, message_count: int, minutes_quiet: float, agent_wrote: bool) -> bool:
    """Whether the safety net rewrites the block: never when the agent wrote it; after a goal or a build; after a
    long conversation (QUIET_MESSAGES or more messages) that has been quiet for QUIET_MINUTES."""
    if agent_wrote:
        return False
    if goal_or_build_ended:
        return True
    return message_count >= QUIET_MESSAGES and minutes_quiet >= QUIET_MINUTES


# --- Reading and writing the block ---------------------------------------------------------------------


def memory_file() -> str:
    return f"{PROJECT_LAYER_FOLDER}/{MEMORY_FILE}"


def state_updated_at(project_folder: Path, environment_id: int) -> datetime | None:
    """When the block was last written through YAAP: the time of the newest commit of the memory file whose message
    starts with COMMIT_PREFIX. None without a repository or such a commit. Blocking (git)."""
    if not has_repository(project_folder):
        return None
    try:
        found = run_git(
            ["log", "-1", "--format=%cI", f"--grep=^{COMMIT_PREFIX}", "--", memory_file()],
            git_place(environment_id, project_folder),
            accept_exit_codes=(0, 128),
        )
    except GitError as error:
        logger.debug("The time of the current state could not be read: %s", error)
        return None
    stamp = found.stdout.strip()
    try:
        return datetime.fromisoformat(stamp).astimezone(UTC) if stamp else None
    except ValueError:
        return None


def save_state(project_folder: Path, environment_id: int, text: str, by: str) -> str:
    """Replace the block with text in one commit of the memory file; returns the block as written. Blocking."""
    if not has_repository(project_folder):
        raise StateError("no_repository", "The project has no git repository, so its current state cannot be kept.")
    problem = check_state(text)
    if problem is not None:
        raise StateError("state_invalid", problem)
    updated = replace_state(read_memory(project_folder), text)
    write_memory(project_folder, environment_id, updated, f"{COMMIT_PREFIX}{by}")
    return read_state(updated) or ""


# --- The safety net ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RefreshSource:
    """What a refresh rewrites the block from: the project, the conversation since a moment, and what happened."""

    project_id: int
    reason: str
    conversation_id: int | None = None
    since: datetime | None = None


@dataclass(frozen=True)
class StateRefreshContext:
    utility: UtilityContext
    workspace_dir: Path


def as_utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def project_folder_of(context: StateRefreshContext, session: Session, project_id: int) -> tuple[Project, Path] | None:
    project = session.get(Project, project_id)
    if project is None:
        return None
    try:
        return project, project_path(context.workspace_dir, project)
    except ProjectError:
        return None


def goal_source(context: StateRefreshContext, conversation_id: int, now: datetime) -> RefreshSource | None:
    """A refresh after the conversation's goal ended, when it is due. Blocking (database and git)."""
    with context.utility.session_factory() as session:
        conversation = session.get(Conversation, conversation_id)
        if conversation is None or conversation.project_id is None:
            return None
        goal = last_ended_goal(session, conversation_id)
        if goal is None or goal.finished_at is None or now - as_utc(goal.finished_at) > GOAL_END_WINDOW:
            return None
        found = project_folder_of(context, session, conversation.project_id)
        if found is None:
            return None
        project, folder = found
        environment_id, started = project.environment_id, as_utc(goal.created_at)
        summary = f"\nIts summary: {goal.summary}" if goal.summary else ""
        reason = f"A goal ended ({goal.status}): {goal.objective}{summary}"
        finished = as_utc(goal.finished_at)
    written = state_updated_at(folder, environment_id)
    # The goal's end is newer than the last refresh for it: a write after the goal ended means it was handled.
    handled = written is not None and written >= finished
    if handled or not refresh_due(True, 0, 0, written is not None and written >= started):
        return None
    return RefreshSource(conversation.project_id, reason, conversation_id, started)


def quiet_source(context: StateRefreshContext, conversation_id: int, now: datetime) -> RefreshSource | None:
    """A refresh of a long conversation that has gone quiet, when it is due. Blocking (database and git)."""
    with context.utility.session_factory() as session:
        conversation = session.get(Conversation, conversation_id)
        if conversation is None or conversation.project_id is None:
            return None
        found = project_folder_of(context, session, conversation.project_id)
        if found is None:
            return None
        project, folder = found
        environment_id = project.environment_id
    written = state_updated_at(folder, environment_id)
    with context.utility.session_factory() as session:
        counted = select(func.count(), func.max(Message.created_at)).where(
            Message.conversation_id == conversation_id, Message.status == "complete"
        )
        if written is not None:
            counted = counted.where(Message.created_at > written)
        count, last = session.execute(counted).one()
    if not count or last is None:
        return None
    minutes_quiet = (now - as_utc(last)).total_seconds() / 60
    if not refresh_due(False, int(count), minutes_quiet, False):
        return None
    return RefreshSource(project.id, "A long conversation went quiet.", conversation_id, written)


def clean_answer(answer: str) -> str:
    """The block from the model's answer: without a code fence or a repeated heading around it."""
    text = answer.strip()
    fenced = FENCE.match(text)
    if fenced is not None:
        text = fenced.group(1).strip()
    lines = text.split("\n")
    if lines and lines[0].lstrip("# ").strip().lower() == STATE_SECTION.lower():
        text = "\n".join(lines[1:]).strip()
    return text


def refresh_prompt(instructions: str, language: str, old: str | None, source: RefreshSource, conversation: str) -> str:
    lead = render(instructions, {"max_chars": str(MAX_STATE_CHARS), "language": language})
    return (
        f"{lead}\n\n<current_state>\n{old or '(none yet)'}\n</current_state>\n\n"
        f"<what_happened>\n{source.reason}\n</what_happened>\n\n"
        f"<conversation>\n{conversation or '(none)'}\n</conversation>"
    )


async def refresh_state(context: StateRefreshContext, source: RefreshSource) -> bool:
    """Rewrite the block through the utility task; True when it was written. An answer that is not a valid block, or
    a block that changed while the model wrote, keeps what the memory holds."""
    with context.utility.session_factory() as session:
        found = project_folder_of(context, session, source.project_id)
        if found is None:
            return False
        project, folder = found
        environment_id = project.environment_id
        language = resolve_languages(session, environment_id, project.id).work
        conversation = (
            conversation_text(session, source.conversation_id, source.since) if source.conversation_id else ""
        )
    if not has_repository(folder):
        return False
    old = read_state(await anyio.to_thread.run_sync(read_memory, folder))
    instructions = utility_instructions(context.utility, environment_id, "state-keeper", "Refresh")
    answer = await run_utility(
        context.utility,
        environment_id,
        refresh_prompt(instructions, language, old, source, conversation),
        source.conversation_id,
        max_tokens=MAX_ANSWER_TOKENS,
        task="current_state",
        project_id=project.id,
    )
    if answer is None:
        return False
    new = clean_answer(answer)
    problem = check_state(new)
    if problem is not None:
        logger.info("The refreshed current state was not used: %s", problem)
        return False
    return await anyio.to_thread.run_sync(write_if_unchanged, folder, environment_id, old, new)


def write_if_unchanged(folder: Path, environment_id: int, old: str | None, new: str) -> bool:
    """Write the refreshed block unless the block changed since it was read: that edit wins. Blocking."""
    current = read_memory(folder)
    if read_state(current) != old:
        return False
    try:
        write_memory(folder, environment_id, replace_state(current, new), f"{COMMIT_PREFIX}refreshed")
    except ProjectMemoryError as error:
        logger.warning("The refreshed current state could not be written: %s", error)
        return False
    return True


# --- Hooks for other work that ends ------------------------------------------------------------------------

PlanEventListener = Callable[[int, str], None]
PlanSource = Callable[[int], RefreshSource | None]
Starter = Callable[[Coroutine[Any, Any, bool]], object]
PLAN_END_EVENTS = frozenset({"done", "failed"})


def plan_event_listener(context: StateRefreshContext, source_of: PlanSource, start: Starter) -> PlanEventListener:
    """A listener for a build's events (plan id, event): when a build is done or failed, the block is refreshed from
    what source_of says about the plan, unless the agent wrote it during the build. start runs the refresh in the
    background (for example BackgroundWork.start); the listener is called on the event loop."""

    def listen(plan_id: int, event: str) -> None:
        if event not in PLAN_END_EVENTS:
            return
        source = source_of(plan_id)
        if source is not None:
            start(refresh_unless_written(context, source))

    return listen


async def refresh_unless_written(context: StateRefreshContext, source: RefreshSource) -> bool:
    """Refresh after a build, unless the block was written since the build started."""
    with context.utility.session_factory() as session:
        found = project_folder_of(context, session, source.project_id)
    if found is None:
        return False
    project, folder = found
    written = await anyio.to_thread.run_sync(state_updated_at, folder, project.environment_id)
    agent_wrote = written is not None and source.since is not None and written >= source.since
    if not refresh_due(True, 0, 0, agent_wrote):
        return False
    return await refresh_state(context, source)
