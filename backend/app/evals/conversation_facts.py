"""What a conversation says about the case it could become.

Read from the database, the project's git history and the checkpoints, without any model:

- **The start.** The project's commit before the first message: the newest commit on the project's current branch
  whose commit time is not after the first user message (`git rev-list -1 --before`). Without one (not a git
  repository, or no commit yet then) the conversation's first checkpoint, the folder just before its first change.
- **The prompt.** The text of the first user message on the conversation's current branch.
- **The changed files.** From the conversation's checkpoints: its first checkpoint against the state after its last
  action (the next checkpoint of the project, or the folder as it is now). Without checkpoints, the paths of its
  successful write_file and edit_file calls. Files of the Blueprint layer (`.blueprint/`) are left out.
- **The commands** its run_command calls ran, and **the usage** of its runs' steps (45.1).
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.blueprint.layers import PROJECT_LAYER_FOLDER
from app.checkpoints.service import CheckpointStore
from app.checkpoints.shadow import CheckpointError
from app.core.chat import load_branch
from app.core.search_index import message_text
from app.evals.cases import workspace_path
from app.evals.models import StartState
from app.evals.project_commands import RanCommand
from app.git.service import GitError, run_git
from app.storage.models import Checkpoint, Conversation, Message
from app.tracing.steps import RunNode, conversation_runs, run_steps

StartSource = Literal["commit", "checkpoint", "none"]
FILE_TOOLS = frozenset({"write_file", "edit_file"})
MAX_CHANGED_FILES = 40


class CaseFromConversationError(Exception):
    """The conversation cannot become a case; the message says why."""


@dataclass(frozen=True)
class ConversationUsage:
    """What the conversation's runs used, sub-runs included."""

    steps: int
    tokens: int
    # In USD; None when no step had a cost (a subscription).
    cost_usd: float | None
    seconds: float


@dataclass(frozen=True)
class ConversationFacts:
    conversation_id: int
    title: str
    prompt: str
    user_messages: int
    final_reply: str
    agent: str | None
    preset: str | None
    start: StartState
    start_source: StartSource
    # Both candidates, so the user can pick the other one.
    commit_before: str | None
    first_checkpoint: int | None
    changed_files: tuple[str, ...]
    # The changed files that no longer exist in the project folder.
    removed_files: tuple[str, ...]
    commands: tuple[RanCommand, ...]
    usage: ConversationUsage | None


def read_conversation(
    session: Session, checkpoints: CheckpointStore, conversation: Conversation, folder: Path
) -> ConversationFacts:
    if conversation.project_id is None:
        raise CaseFromConversationError("Only a conversation in a project can become an eval case.")
    branch = load_branch(session, conversation.id, conversation.head_message_id)
    users = [message for message in branch if message.role == "user"]
    first = next((message for message in users if message_text(message)), None)
    if first is None:
        raise CaseFromConversationError("The conversation has no message with text to use as the prompt.")
    replies = [message_text(message) for message in branch if message.role == "assistant"]
    first_checkpoint, last_checkpoint = conversation_checkpoints(session, conversation)
    commit = commit_before(folder, first.created_at)
    if commit is not None:
        start, source = StartState(commit=commit), "commit"
    elif first_checkpoint is not None:
        start, source = StartState(checkpoint=first_checkpoint.id), "checkpoint"
    else:
        start, source = StartState(), "none"
    changed = changed_by_checkpoints(session, checkpoints, first_checkpoint, last_checkpoint, folder)
    if changed is None:
        changed = changed_by_tool_calls(branch)
    changed = tuple(path for path in changed if not path.startswith(f"{PROJECT_LAYER_FOLDER}/"))[:MAX_CHANGED_FILES]
    return ConversationFacts(
        conversation_id=conversation.id,
        title=conversation.title,
        prompt=message_text(first),
        user_messages=len(users),
        final_reply=next((text for text in reversed(replies) if text), ""),
        agent=conversation.agent,
        preset=conversation.preset,
        start=start,
        start_source=source,
        commit_before=commit,
        first_checkpoint=first_checkpoint.id if first_checkpoint is not None else None,
        changed_files=changed,
        removed_files=tuple(path for path in changed if not (folder / path).exists()),
        commands=ran_commands(branch),
        usage=conversation_usage(session, conversation.id),
    )


def commit_before(folder: Path, moment: datetime) -> str | None:
    """The newest commit on the project's current branch made at or before the moment; None without one."""
    if not (folder / ".git").exists():
        return None
    when = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    stamp = when.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S +0000")
    try:
        result = run_git(["rev-list", "-1", f"--before={stamp}", "HEAD", "--"], folder)
    except GitError:
        # A repository without commits has no HEAD to list.
        return None
    return result.stdout.strip() or None


def conversation_checkpoints(
    session: Session, conversation: Conversation
) -> tuple[Checkpoint | None, Checkpoint | None]:
    """The conversation's first and last checkpoint taken before an action (not a restore or an editor save)."""
    query = select(Checkpoint).where(
        Checkpoint.conversation_id == conversation.id,
        Checkpoint.project_id == conversation.project_id,
        Checkpoint.kind == "action",
    )
    first = session.scalar(query.order_by(Checkpoint.id))
    last = session.scalar(query.order_by(Checkpoint.id.desc()))
    return first, last


def changed_by_checkpoints(
    session: Session,
    checkpoints: CheckpointStore,
    first: Checkpoint | None,
    last: Checkpoint | None,
    folder: Path,
) -> tuple[str, ...] | None:
    """The paths that differ between the first checkpoint and the state after the last action; None without
    checkpoints or when the shadow repository cannot tell."""
    if first is None or last is None:
        return None
    try:
        after = checkpoints.next_tree(session, last, folder)
        return tuple(checkpoints.repository(first.project_id, folder).changed_paths(first.tree, after))
    except (CheckpointError, OSError):
        return None


def tool_calls(branch: list[Message]) -> list[dict[str, object]]:
    return [
        block
        for message in branch
        if message.role == "assistant"
        for block in message.content
        if block.get("type") == "tool_call" and isinstance(block.get("input"), dict)
    ]


def changed_by_tool_calls(branch: list[Message]) -> tuple[str, ...]:
    paths: list[str] = []
    for block in tool_calls(branch):
        arguments = block["input"]
        if block.get("name") not in FILE_TOOLS or block.get("is_error") or not isinstance(arguments, dict):
            continue
        path = workspace_path(arguments.get("path"), "path", [])
        if path is not None and path not in paths:
            paths.append(path)
    return tuple(paths)


def ran_commands(branch: list[Message]) -> tuple[RanCommand, ...]:
    commands: list[RanCommand] = []
    for block in tool_calls(branch):
        arguments = block["input"]
        command = arguments.get("command") if isinstance(arguments, dict) else None
        if block.get("name") == "run_command" and isinstance(command, str):
            commands.append(RanCommand(command, bool(block.get("is_error"))))
    return tuple(commands)


def conversation_usage(session: Session, conversation_id: int) -> ConversationUsage | None:
    """The steps, tokens, cost and time of the conversation's runs; None when no step was recorded."""
    roots = conversation_runs(session, conversation_id)
    steps = [step for node in all_runs(roots) for step in run_steps(session, node.id)]
    if not steps:
        return None
    costs = [step.cost for step in steps if step.cost is not None]
    seconds = sum(
        (node.finished_at - node.started_at).total_seconds() for node in roots if node.finished_at is not None
    )
    return ConversationUsage(
        steps=len(steps),
        tokens=sum(
            step.usage.input_tokens
            + step.usage.output_tokens
            + step.usage.cache_read_tokens
            + step.usage.cache_write_tokens
            for step in steps
        ),
        cost_usd=round(sum(costs), 6) if costs else None,
        seconds=max(seconds, 0.0),
    )


def all_runs(nodes: list[RunNode]) -> list[RunNode]:
    found: list[RunNode] = []
    for node in nodes:
        found.append(node)
        found.extend(all_runs(node.children))
    return found
