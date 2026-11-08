"""Where YAAP's data folder is on the user's computer, so the agent can name a project folder as the user sees it.

Inside its container YAAP sees its data as /data. The sandbox helper reads the source of that bind mount from Docker
(GET /host/data), and YAAP asks for it once when it starts. Docker Desktop on Windows reports it as C:\\YAAP\\data, or,
depending on its version and backend, in a Linux form of that path; `host_path` turns those into the Windows form and
leaves a Linux host's path as it is. Without the helper (YAAP_SANDBOX_URL unset: YAAP runs directly on the computer,
as in development with YAAP_RUNNER=off), YAAP's own data folder already is the user's path.
"""

import asyncio
import logging
import re
from pathlib import Path

from app.sandbox.client import HelperClient, SandboxUnavailable

log = logging.getLogger(__name__)

WINDOWS_DRIVE = re.compile(r"^([A-Za-z]):(?:[\\/](.*))?$", re.DOTALL)
WINDOWS_SHARE = re.compile(r"^\\\\[^\\/]+[\\/]")
# Docker Desktop's and WSL's Linux names for a Windows drive: /run/desktop/mnt/host/c/..., /mnt/c/..., /host_mnt/c/...
DRIVE_FORMS = re.compile(r"^(?:/run/desktop/mnt/host|/mnt|/host_mnt)/([A-Za-z])(?:/(.*))?$", re.DOTALL)
# Other paths inside Docker Desktop's own machine, which mean nothing on the user's computer.
DOCKER_DESKTOP_INTERNAL = ("/run/desktop/", "/host_mnt/", "/mnt/wsl/", "/var/lib/docker/")


def windows_path(drive: str, rest: str) -> str:
    parts = [part for part in re.split(r"[\\/]+", rest) if part]
    return f"{drive.upper()}:\\" + "\\".join(parts)


def host_path(source: str) -> str | None:
    """The user's form of a host folder Docker reports; None when the form is not recognised."""
    source = source.strip()
    if not source or any(character in source for character in "\r\n\0"):
        return None
    if found := WINDOWS_DRIVE.match(source):
        return windows_path(found.group(1), found.group(2) or "")
    if WINDOWS_SHARE.match(source):
        return source.rstrip("\\/")
    if found := DRIVE_FORMS.match(source):
        return windows_path(found.group(1), found.group(2) or "")
    if source.startswith(DOCKER_DESKTOP_INTERNAL) or not source.startswith("/"):
        return None
    return "/" + "/".join(part for part in source.split("/") if part)


def is_windows(path: str) -> bool:
    return bool(WINDOWS_DRIVE.match(path) or WINDOWS_SHARE.match(path))


def project_host_folder(host_data: str, project_folder: str) -> str:
    """The project's folder on the user's computer: the data folder, workspace, then `<environment>/<project>`
    (Project.folder), joined with the host's separator."""
    separator = "\\" if is_windows(host_data) else "/"
    parts = ["workspace", *(part for part in project_folder.split("/") if part)]
    return host_data.rstrip("\\/") + separator + separator.join(parts)


async def discover_host_data(
    helper: HelperClient | None, data_dir: Path, attempts: int = 5, wait: float = 3.0
) -> str | None:
    """YAAP's data folder on the user's computer, or None when it cannot be known. Never raises for an absent or
    old helper: the agent then simply gets no such line."""
    if helper is None:
        return host_path(str(data_dir.resolve()))
    for attempt in range(attempts):
        try:
            source = await helper.host_data()
        except SandboxUnavailable as error:
            # The helper may start a moment after YAAP; an image from before /host/data answers 404 every time.
            if attempt + 1 < attempts:
                await asyncio.sleep(wait)
                continue
            log.warning("YAAP does not know where its data folder is on the host: %s", error)
            return None
        found = host_path(source) if source else None
        if found is None:
            log.warning("YAAP does not know where its data folder is on the host (Docker reports %r).", source)
        return found
    return None
