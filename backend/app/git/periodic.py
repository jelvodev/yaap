"""Fetch every repository with a remote now and then, so "behind" shows changes made elsewhere.

Fetching only updates the remote-tracking branches; it never changes the working folder. A repository that cannot be
fetched (no network, an expired token) is skipped quietly and tried again next time.
"""

import asyncio
import logging
from pathlib import Path

import anyio
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.credentials.crypto import CredentialCipher, CredentialDecryptionError
from app.credentials.store import read_credential_value
from app.blueprint.levels import environment_layer, global_layer
from app.git.service import GitError, fetch, remote_url
from app.projects.service import project_path
from app.storage.models import Environment, Project

logger = logging.getLogger(__name__)

FETCH_INTERVAL_SECONDS = 15 * 60


def repositories_with_remotes(
    session: Session, cipher: CredentialCipher, blueprint_dir: Path, workspace_dir: Path
) -> list[tuple[Path, str | None]]:
    """Each repository that has a remote, with the GitHub token of the environment it belongs to."""
    found: list[tuple[Path, str | None]] = []
    tokens: list[str] = []
    for environment in session.scalars(select(Environment).order_by(Environment.id)):
        try:
            token = read_credential_value(session, cipher, environment.id, "github_token", used_by="Periodic git fetch")
        except CredentialDecryptionError:
            token = None
        if token:
            tokens.append(token)
        folders = [environment_layer(blueprint_dir, environment).root]
        folders += [
            project_path(workspace_dir, project)
            for project in session.scalars(select(Project).where(Project.environment_id == environment.id))
        ]
        found += [(folder, token) for folder in folders if (folder / ".git").is_dir() and remote_url(folder)]
    # The global layer belongs to no environment; it is fetched with the first GitHub token there is.
    global_root = global_layer(blueprint_dir).root
    if (global_root / ".git").is_dir() and remote_url(global_root):
        found.insert(0, (global_root, tokens[0] if tokens else None))
    return found


def fetch_all(session_factory: sessionmaker[Session], cipher: CredentialCipher, blueprint_dir: Path, workspace_dir: Path) -> int:
    with session_factory() as session:
        repositories = repositories_with_remotes(session, cipher, blueprint_dir, workspace_dir)
    fetched = 0
    for folder, token in repositories:
        try:
            fetch(folder, token)
            fetched += 1
        except GitError as error:
            logger.info("Periodic fetch of %s skipped: %s", folder.name, error)
    return fetched


async def fetch_periodically(
    session_factory: sessionmaker[Session], cipher: CredentialCipher, blueprint_dir: Path, workspace_dir: Path
) -> None:
    while True:
        await asyncio.sleep(FETCH_INTERVAL_SECONDS)
        try:
            await anyio.to_thread.run_sync(fetch_all, session_factory, cipher, blueprint_dir, workspace_dir)
        except Exception:
            logger.exception("Periodic fetch failed.")
