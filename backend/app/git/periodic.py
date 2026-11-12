"""Fetch every repository with a remote now and then, so "behind" shows changes made elsewhere.

Fetching only updates the remote-tracking branches; it never changes the working folder. It happens in the
repository's transport, and the work repository (so the runner) is only touched when the remote changed. A repository
that cannot be fetched (no network, an expired token) is skipped quietly and tried again next time.
"""

import asyncio
import logging
from functools import partial
from pathlib import Path

import anyio
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.credentials.crypto import CredentialCipher, CredentialDecryptionError
from app.credentials.store import read_credential_value
from app.blueprint.levels import environment_layer, global_layer
from app.git.place import GitPlace, git_place
from app.git.remote import fetch_changes
from app.git.service import GitError, remote_url
from app.projects.service import project_path
from app.storage.models import Environment, Project

logger = logging.getLogger(__name__)

FETCH_INTERVAL_SECONDS = 15 * 60


# A repository folder, the environment it belongs to (None for the global layer) and the GitHub token to fetch with.
PeriodicTarget = tuple[Path, int | None, str | None]


def repositories_with_remotes(
    session: Session, cipher: CredentialCipher, blueprint_dir: Path, workspace_dir: Path
) -> list[PeriodicTarget]:
    """Each repository that has a remote, with the GitHub token of the environment it belongs to. The remote is read
    in YAAP, where the fetch itself runs: both read only the repository's config (checked first, with git's fixed safe
    settings) and never the work tree, so no filter can run, and asking the runner would wake every environment's
    runner each pass. Synchronous: call it from a worker thread."""
    found: list[PeriodicTarget] = []
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
        for folder in folders:
            if not (folder / ".git").is_dir():
                continue
            if remote_url(folder):
                found.append((folder, environment.id, token))
    # The global layer belongs to no environment and is outside the workspace; it is fetched with the first GitHub
    # token there is.
    global_root = global_layer(blueprint_dir).root
    if (global_root / ".git").is_dir() and remote_url(global_root):
        found.insert(0, (global_root, None, tokens[0] if tokens else None))
    return found


def place_for(environment_id: int | None, folder: Path) -> GitPlace:
    """Where git runs for the folder; the global layer belongs to no environment and is outside the workspace."""
    return GitPlace(folder) if environment_id is None else git_place(environment_id, folder)


def fetch_all(session_factory: sessionmaker[Session], cipher: CredentialCipher, blueprint_dir: Path, workspace_dir: Path) -> int:
    with session_factory() as session:
        repositories = repositories_with_remotes(session, cipher, blueprint_dir, workspace_dir)
    fetched = 0
    for folder, environment_id, token in repositories:
        try:
            fetch_changes(folder, partial(place_for, environment_id, folder), token)
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
