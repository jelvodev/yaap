"""A project's files in the interface: where they are, browsing and searching.

The Files tab works through the same target interface as the agent's file tools (`TargetFiles`), so it shows the
project folder in YAAP's container or the project's folder on a computer over SSH, with the same
confinement (no paths outside the folder, no writes into `.git`) and the same skipped folders.
"""

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from sqlalchemy.orm import Session

from app.agent.file_tools import SKIPPED_FOLDERS
from app.core.settings import SETTING_DEFINITIONS, resolve_setting
from app.credentials.crypto import CredentialCipher
from app.projects.service import project_path
from app.storage.models import Project, Target
from app.targets.base import FileEntry, TargetError, TargetFiles
from app.targets.container import CONTAINER
from app.targets.service import settings_for, working_folder
from app.targets.ssh import CONNECTIONS, SshTarget

# One folder level shows at most this many entries.
MAX_LISTED = 2000
# Searching by name walks at most this many entries.
MAX_WALKED = 20_000
MAX_RESULTS = 200
SEARCH_LINE = re.compile(r"^(.*?):(\d+): (.*)$", re.DOTALL)


@dataclass
class ProjectFiles:
    files: TargetFiles
    # "container" or "ssh".
    location: str
    # Where the files are, for the user: YAAP's container, or the computer's name.
    label: str
    # The project folder in YAAP's container, for checkpoints; None on a computer over SSH (no checkpoints there).
    folder: Path | None


def connected(key: str) -> bool:
    connection = CONNECTIONS.open.get(key)
    return connection is not None and not connection.is_closed()


def project_files(session: Session, cipher: CredentialCipher, workspace_dir: Path, project: Project) -> ProjectFiles:
    """The project's files where its conversations work: its target and folder there, or YAAP's container.

    Raises TargetError when the project's target is gone or not set up, or the folder is not allowed on it.
    """
    value = resolve_setting(session, SETTING_DEFINITIONS["target"], project.environment_id, project.id).value
    # A sandbox works on the project folder in YAAP itself (its /workspace), so the tab shows that and
    # never has to start the sandbox.
    if isinstance(value, dict) and value.get("id") and not value.get("sandbox"):
        target = session.get(Target, int(value["id"]))
        if target is None or target.environment_id != project.environment_id:
            raise TargetError("The project's target no longer exists. Choose another in the project settings.")
        if not target.host_key:
            raise TargetError(f"The target {target.name} is not set up yet: test it in Settings › Targets first.")
        root = working_folder(target, str(value.get("folder") or "").strip() or None)
        key = f"target-{target.id}"
        # The key is read from the vault each time, but only a new connection counts as a use of it.
        used_by = "" if connected(key) else f"Files of project {project.name}"
        runtime = SshTarget(key, settings_for(session, cipher, target, used_by))
        return ProjectFiles(runtime.files(root), "ssh", target.name, None)
    folder = project_path(workspace_dir, project)
    return ProjectFiles(CONTAINER.files(str(folder)), "container", "YAAP's container", folder)


def name_of(path: str) -> str:
    return PurePosixPath(path).name or path


async def list_folder(files: TargetFiles, path: str) -> tuple[str, list[FileEntry], bool]:
    """One level of a folder, folders first, without .git and dependency folders; and whether it was cut off."""
    folder = files.check(path or ".")
    found = await files.stat(folder)
    if found is None or not found.is_dir:
        raise TargetError(f"{path} is not a folder.")
    entries = await files.walk(folder, False, SKIPPED_FOLDERS, MAX_LISTED)
    entries.sort(key=lambda entry: (not entry.is_dir, name_of(entry.path).lower()))
    return folder, entries[:MAX_LISTED], len(entries) > MAX_LISTED


async def find_by_name(files: TargetFiles, query: str) -> tuple[list[FileEntry], bool]:
    """Files and folders whose name contains the query (case-insensitive)."""
    needle = query.lower()
    walked = await files.walk(".", True, SKIPPED_FOLDERS, MAX_WALKED)
    found = [entry for entry in walked if needle in name_of(entry.path).lower()]
    found.sort(key=lambda entry: (entry.path.count("/"), entry.path.lower()))
    return found[:MAX_RESULTS], len(found) > MAX_RESULTS or len(walked) > MAX_WALKED


async def find_in_content(files: TargetFiles, query: str) -> tuple[list[tuple[str, int, str]], bool]:
    """Lines that contain the query (case-insensitive), with the agent's search: (path, line, text)."""
    lines = await files.search("(?i)" + re.escape(query), ".", "*", MAX_RESULTS, SKIPPED_FOLDERS)
    matches = []
    for line in lines:
        parsed = SEARCH_LINE.match(line)
        if parsed:
            matches.append((parsed.group(1), int(parsed.group(2)), parsed.group(3)))
    return matches, len(lines) >= MAX_RESULTS
