"""The repository map in a reply's system prompt.

The map is made through the run's own file access and command executor (`tool_context.files` and `executor`), so it
describes the folder the tools work on: the project, an eval case's workspace, or the folder on a computer over SSH.
It is a cacheable part right after the other cacheable parts; the store keeps one map per revision, so the text stays
the same, and the prompt cache holds, until the commit changes.

With context_savings on, the key of the conversation's first map is kept in a `repo_map` block on the newest user
message, and later replies reuse that map's text while the working tree changes, until the next trim or summary.
"""

import logging

import anyio
from sqlalchemy.orm import Session

from app.agent import file_tools
from app.agent.runs import AgentRun
from app.agent.tool_context import executor, files, project_root
from app.core.chat_history import load_branch
from app.core.context_savings import context_savings_on
from app.core.prompts import after_cacheable
from app.core.reply_services import ReplyServices
from app.core.reply_setup import ReplySetup
from app.core.settings import SETTING_DEFINITIONS, resolve_setting
from app.core.summaries import latest_summary
from app.core.trimming import latest_trim
from app.outline.symbols import Outliner
from app.providers.base import SystemPart
from app.repomap.builder import MapSource, build_repo_map
from app.repomap.store import RepoMapStore, store_folder
from app.storage.models import Conversation, Message
from app.targets.base import TargetError

logger = logging.getLogger(__name__)


def map_outliner(_run: AgentRun) -> Outliner:
    """Where the map's public symbols come from: read_file's outliner, so a language server's document symbols that
    take its place serve the map as well. The run is passed for an outliner that works per project."""
    return file_tools.OUTLINER


def map_location(run: AgentRun) -> str:
    kind = str(getattr(run.target, "kind", "container"))
    label = str(getattr(getattr(run.target, "settings", None), "label", "") or "")
    return f"{kind}:{label}:{run.target_root or project_root(run)}"


def repo_map_enabled(session: Session, setup: ReplySetup) -> bool:
    project = setup.agent_project
    if project is None:
        return False
    return resolve_setting(session, SETTING_DEFINITIONS["repo_map"], setup.environment.id, project.id).value == "on"


def kept_map_key(branch: list[Message], location: str) -> str | None:
    """The store key of the map kept for the conversation: the newest `repo_map` block after the latest trim or
    summary, since the model's history up to there is replaced and the prompt cache starts over anyway. A map made
    for another folder (the conversation moved to another project or computer) is not kept."""
    floor = max(-1 if index is None else index for index in (latest_trim(branch), latest_summary(branch)))
    for index in range(len(branch) - 1, floor, -1):
        for block in reversed(branch[index].content):
            if block.get("type") == "repo_map" and isinstance(block.get("key"), str):
                return block["key"] if block.get("location") == location else None
    return None


def keep_map_key(session: Session, branch: list[Message], key: str, location: str) -> None:
    """Record the key on the newest message of the branch, the one this reply answers."""
    if not branch:
        return
    message = branch[-1]
    others = [block for block in message.content if block.get("type") != "repo_map"]
    message.content = [*others, {"type": "repo_map", "key": key, "location": location}]
    session.commit()


def conversation_branch(session: Session, run: AgentRun) -> list[Message]:
    conversation = session.get(Conversation, run.conversation_id)
    return load_branch(session, run.conversation_id, conversation.head_message_id) if conversation else []


def repo_map_part(services: ReplyServices, session: Session, setup: ReplySetup, run: AgentRun) -> SystemPart | None:
    """The map of the run's folder, or None when the setting is off or the map cannot be made in time. Called from a
    worker thread, like the rest of starting a run."""
    if run.project_folder is None or not repo_map_enabled(session, setup):
        return None
    store = RepoMapStore(store_folder(services.data_dir))
    kept = context_savings_on(session, setup.environment.id)
    branch = conversation_branch(session, run) if kept else []
    location = map_location(run)
    key = kept_map_key(branch, location)
    stored = store.get(key) if key else None
    if stored:
        return SystemPart(text=stored, cacheable=True, source="repo_map")
    source = MapSource(files(run), executor(run), map_outliner(run), location)
    try:
        made = anyio.from_thread.run(build_repo_map, source, store)
    except (TimeoutError, TargetError, OSError) as error:
        # A reply never waits on or fails for its map; the agent can still look around with its tools.
        logger.warning("No repository map for conversation %s: %s", run.conversation_id, error or type(error).__name__)
        return None
    if kept and made.text:
        keep_map_key(session, branch, made.key, location)
    return SystemPart(text=made.text, cacheable=True, source="repo_map") if made.text else None


def with_repo_map(
    services: ReplyServices, session: Session, setup: ReplySetup, run: AgentRun, system: list[SystemPart]
) -> list[SystemPart]:
    return after_cacheable(system, repo_map_part(services, session, setup, run))
