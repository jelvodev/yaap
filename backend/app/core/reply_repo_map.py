"""The repository map in a reply's system prompt.

The map is made through the run's own file access and command executor (`tool_context.files` and `executor`), so it
describes the folder the tools work on: the project, an eval case's workspace, or the folder on a computer over SSH.
It is a cacheable part right after the other cacheable parts; the store keeps one map per revision, so the text stays
the same, and the prompt cache holds, until the commit changes.
"""

import logging

import anyio
from sqlalchemy.orm import Session

from app.agent import file_tools
from app.agent.runs import AgentRun
from app.agent.tool_context import executor, files, project_root
from app.core.prompts import after_cacheable
from app.core.reply_services import ReplyServices
from app.core.reply_setup import ReplySetup
from app.core.settings import SETTING_DEFINITIONS, resolve_setting
from app.outline.symbols import Outliner
from app.providers.base import SystemPart
from app.repomap.builder import MapSource, build_repo_map
from app.repomap.store import RepoMapStore, store_folder
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


def repo_map_part(services: ReplyServices, session: Session, setup: ReplySetup, run: AgentRun) -> SystemPart | None:
    """The map of the run's folder, or None when the setting is off or the map cannot be made in time. Called from a
    worker thread, like the rest of starting a run."""
    if run.project_folder is None or not repo_map_enabled(session, setup):
        return None
    source = MapSource(files(run), executor(run), map_outliner(run), map_location(run))
    store = RepoMapStore(store_folder(services.data_dir))
    try:
        made = anyio.from_thread.run(build_repo_map, source, store)
    except (TimeoutError, TargetError, OSError) as error:
        # A reply never waits on or fails for its map; the agent can still look around with its tools.
        logger.warning("No repository map for conversation %s: %s", run.conversation_id, error or type(error).__name__)
        return None
    return SystemPart(text=made.text, cacheable=True, source="repo_map") if made.text else None


def with_repo_map(
    services: ReplyServices, session: Session, setup: ReplySetup, run: AgentRun, system: list[SystemPart]
) -> list[SystemPart]:
    return after_cacheable(system, repo_map_part(services, session, setup, run))
