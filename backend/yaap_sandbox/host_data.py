"""Where YAAP's data folder is on the host: the source of the /data bind mount of YAAP's own container.

YAAP tells the agent where a project folder is on the user's computer (app/sandbox/host_path.py), but inside its
container it only sees /data. The helper finds YAAP's container as the service `yaap` of its own Compose project and
answers the mount's source as Docker reports it; YAAP turns that into the form the user knows. Reading YAAP's container
and the helper's own are the only reads of containers without YAAP's sandbox label, and nothing is changed.
"""

import os
from typing import Any

from yaap_sandbox.docker_api import DockerApi

COMPOSE_PROJECT = "com.docker.compose.project"
COMPOSE_SERVICE = "com.docker.compose.service"
YAAP_SERVICE = "yaap"
DATA = "/data"


def data_mount_source(container: dict[str, Any]) -> str:
    """The host source of the container's /data bind mount; empty for a volume or no such mount."""
    for mount in container.get("Mounts") or []:
        if mount.get("Destination") == DATA and mount.get("Type") == "bind":
            return str(mount.get("Source") or "")
    return ""


async def yaap_data_source(docker: DockerApi, hostname: str | None = None) -> str:
    """The source of YAAP's /data mount, found through the helper's own container; empty when it cannot be found."""
    own = await docker.inspect_container(hostname or os.environ.get("HOSTNAME", ""))
    labels = ((own or {}).get("Config") or {}).get("Labels") or {}
    project = labels.get(COMPOSE_PROJECT)
    if not project:
        return ""
    wanted = {COMPOSE_PROJECT: project, COMPOSE_SERVICE: YAAP_SERVICE}
    for container in await docker.list_containers([f"{name}={value}" for name, value in wanted.items()]):
        # Checked again: the filter is Docker's, the decision is the helper's.
        found = container.get("Labels") or {}
        if all(found.get(name) == value for name, value in wanted.items()):
            source = data_mount_source(container)
            if source:
                return source
    return ""
