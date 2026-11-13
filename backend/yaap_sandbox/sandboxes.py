"""Sandbox containers: what the helper may do with them, and nothing else.

A sandbox is known by a key YAAP chooses (`p12` for project 12, `e3` for the runner of environment 3, which mounts
the environment's folder instead of one project's; the helper treats both the same). Its container is named
`<instance>-sandbox-<key>`, where the instance is the Compose project of the helper (`yaap`), so two installations on
one Docker never touch each other's sandboxes. Every operation looks the container up by that name and refuses it
unless it carries YAAP's sandbox label and this instance's label: a container someone else made with that name is
never started, stopped or removed. A new sandbox is made only from the sandbox image, which must carry its own label.

The sandbox gets the project folder as /workspace (a bind mount of the folder under YAAP's workspace on the host), the
public key YAAP signs in with, and a host key YAAP made, so YAAP can pin it without asking anyone. Its network is the
internal sandbox network (no route outside) unless the project switched the network on.
"""

import asyncio
import base64
import hashlib
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from yaap_sandbox.docker_api import DockerApi, DockerError, is_local_name

log = logging.getLogger("yaap_sandbox")

LABEL = "org.yaap.sandbox"
KEY_LABEL = "org.yaap.sandbox.key"
INSTANCE_LABEL = "org.yaap.sandbox.instance"
CONFIG_LABEL = "org.yaap.sandbox.config"
NETWORK_LABEL = "org.yaap.sandbox.network"
# The label the sandbox image carries (docker/sandbox/Dockerfile).
IMAGE_LABEL = "org.yaap.sandbox.image"

KEY = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
FOLDER_PART = re.compile(r"^[A-Za-z0-9._ -]{1,120}$")
AUTHORIZED_KEY = re.compile(r"^ssh-ed25519 [A-Za-z0-9+/]+={0,3}( [^\r\n]{0,200})?$")
PRIVATE_KEY = re.compile(
    r"^-----BEGIN OPENSSH PRIVATE KEY-----\r?\n[A-Za-z0-9+/=\r\n]{20,4000}-----END OPENSSH PRIVATE KEY-----\r?\n?$"
)
WINDOWS_PATH = re.compile(r"^([A-Za-z]):[\\/]")
SSH_PORT = 22
WORKSPACE = "/workspace"


class SandboxError(Exception):
    """A request the helper does not carry out; status is the HTTP status the API answers with."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class Config:
    image: str = "yaap-sandbox:local"
    # Where the helper sees YAAP's workspace (read-only), to check folders exist before mounting them.
    workspace_dir: Path = Path(WORKSPACE)
    # The same folder as the host shows it, for the bind mount; found from the helper's own mount when empty.
    workspace_host: str = ""
    instance: str = ""
    network_off: str = "yaap-sandbox-offline"
    network_on: str = "yaap-sandbox-online"
    cpus: float = 2.0
    memory: int = 4 * 1024**3
    pids: int = 2048
    # How long a start waits for the runner's download before it answers that the download goes on; below the
    # time YAAP waits for a start.
    pull_wait: float = 150.0

    @classmethod
    def from_environment(cls) -> "Config":
        get = os.environ.get
        return cls(
            image=get("YAAP_SANDBOX_IMAGE", "yaap-sandbox:local"),
            workspace_dir=Path(get("YAAP_SANDBOX_WORKSPACE_DIR", WORKSPACE)),
            workspace_host=get("YAAP_SANDBOX_WORKSPACE_HOST", ""),
            instance=get("YAAP_SANDBOX_INSTANCE", ""),
            network_off=get("YAAP_SANDBOX_NETWORK_OFF", "yaap-sandbox-offline"),
            network_on=get("YAAP_SANDBOX_NETWORK_ON", "yaap-sandbox-online"),
            cpus=float(get("YAAP_SANDBOX_CPUS", "2")),
            memory=parse_size(get("YAAP_SANDBOX_MEMORY", "4g")),
            pids=int(get("YAAP_SANDBOX_PIDS", "2048")),
        )


def parse_size(value: str) -> int:
    """4g, 512m or a number of bytes."""
    value = value.strip().lower()
    units = {"k": 1024, "m": 1024**2, "g": 1024**3}
    if value and value[-1] in units:
        return int(float(value[:-1]) * units[value[-1]])
    return int(value)


def docker_path(source: str) -> str:
    """A host folder as Docker's Linux engine knows it. Docker Desktop on Windows reports a bind source as
    C:\\YAAP\\data; its engine finds that folder under /run/desktop/mnt/host/c/YAAP/data."""
    found = WINDOWS_PATH.match(source)
    if found:
        rest = source[3:].replace("\\", "/").strip("/")
        return f"/run/desktop/mnt/host/{found.group(1).lower()}/{rest}".rstrip("/")
    return source.rstrip("/") or "/"


@dataclass
class StartSpec:
    """What YAAP asks for; the helper builds the rest of the container itself."""

    folder: str
    network: bool
    authorized_key: str
    # The private host key (OpenSSH format), used only when the container has to be created.
    host_key: str

    def check(self) -> None:
        parts = self.folder.split("/")
        if not self.folder or any(part in ("", ".", "..") or not FOLDER_PART.match(part) for part in parts):
            raise SandboxError(422, "The folder must be a path inside YAAP's workspace, such as default/site.")
        if not AUTHORIZED_KEY.match(self.authorized_key.strip()):
            raise SandboxError(422, "The authorized key must be one ssh-ed25519 public key.")
        if not PRIVATE_KEY.match(self.host_key):
            raise SandboxError(422, "The host key must be an OpenSSH private key.")

    def config_hash(self, image: str, image_id: str = "") -> str:
        """What decides whether an existing container still fits; the host key does not, it stays with the
        container. The image's id is part of it, so a rebuilt image (an update, extra packages) makes the container
        again on its next start."""
        text = "\n".join([image, image_id, self.folder, "on" if self.network else "off", self.authorized_key.strip()])
        return hashlib.sha256(text.encode()).hexdigest()[:32]


def cpu_percent(stats: dict[str, Any]) -> float | None:
    """Docker's own formula: the container's share of the host's CPU time between two samples, times the CPUs."""
    try:
        cpu, previous = stats["cpu_stats"], stats["precpu_stats"]
        used = cpu["cpu_usage"]["total_usage"] - previous["cpu_usage"]["total_usage"]
        system = cpu["system_cpu_usage"] - previous.get("system_cpu_usage", 0)
        cpus = cpu.get("online_cpus") or len(cpu["cpu_usage"].get("percpu_usage") or []) or 1
    except (KeyError, TypeError):
        return None
    if system <= 0 or used < 0:
        return 0.0
    return round(used / system * cpus * 100, 1)


def memory_used(stats: dict[str, Any]) -> tuple[int | None, int | None]:
    """Used memory without the page cache (as `docker stats` shows it), and the limit."""
    memory = stats.get("memory_stats") or {}
    usage = memory.get("usage")
    if usage is None:
        return None, memory.get("limit")
    details = memory.get("stats") or {}
    cache = details.get("inactive_file", details.get("total_inactive_file", 0))
    return int(usage - cache), memory.get("limit")


@dataclass
class Sandboxes:
    docker: DockerApi
    config: Config
    locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    # The download of the configured image, shared by every start that needs it.
    pull: "asyncio.Task[str | None] | None" = None

    async def discover(self, hostname: str | None = None) -> None:
        """Fill in the instance and the host's workspace folder from the helper's own container, when not set.

        This inspection of the helper itself and the lookup of YAAP's /data mount (host_data.py) are the only times it
        reads a container without YAAP's label.
        """
        if self.config.instance and self.config.workspace_host:
            return
        own = await self.docker.inspect_container(hostname or os.environ.get("HOSTNAME", ""))
        if own is None:
            raise SandboxError(503, "The helper cannot find its own container; set YAAP_SANDBOX_INSTANCE and YAAP_SANDBOX_WORKSPACE_HOST.")
        if not self.config.instance:
            labels = (own.get("Config") or {}).get("Labels") or {}
            self.config.instance = labels.get("com.docker.compose.project") or "yaap"
        if not self.config.workspace_host:
            for mount in own.get("Mounts") or []:
                if mount.get("Destination") == str(PurePosixPath(self.config.workspace_dir)):
                    self.config.workspace_host = str(mount.get("Source") or "")
            if not self.config.workspace_host:
                raise SandboxError(503, f"The helper has no {self.config.workspace_dir} mount; see docker-compose.yml.")

    @property
    def instance(self) -> str:
        return self.config.instance or "yaap"

    def name(self, key: str) -> str:
        if not KEY.match(key):
            raise SandboxError(422, "A sandbox key uses lowercase letters, digits and dashes (at most 40).")
        return f"{self.instance}-sandbox-{key}"

    def lock(self, key: str) -> asyncio.Lock:
        return self.locks.setdefault(key, asyncio.Lock())

    def is_ours(self, info: dict[str, Any], key: str | None = None) -> bool:
        labels = (info.get("Config") or {}).get("Labels") or {}
        return (
            labels.get(LABEL) == "1"
            and labels.get(INSTANCE_LABEL) == self.instance
            and (key is None or labels.get(KEY_LABEL) == key)
        )

    async def find(self, key: str) -> dict[str, Any] | None:
        """The sandbox's container, None when there is none; refuses a container with its name but not its labels."""
        info = await self.docker.inspect_container(self.name(key))
        if info is not None and not self.is_ours(info, key):
            raise SandboxError(403, f"{self.name(key)} is not a sandbox of this YAAP; the helper leaves it alone.")
        return info

    def host_folder(self, folder: str) -> str:
        if not self.config.workspace_host:
            raise SandboxError(503, "The helper does not know where YAAP's workspace is on the host.")
        local = (self.config.workspace_dir / folder).resolve()
        root = self.config.workspace_dir.resolve()
        if not local.is_relative_to(root) or local == root:
            raise SandboxError(422, "The folder must be inside YAAP's workspace.")
        if not local.is_dir():
            raise SandboxError(404, f"The folder {folder} does not exist in YAAP's workspace.")
        return docker_path(self.config.workspace_host) + "/" + folder

    async def download_image(self) -> str | None:
        """Pull the configured image; the error as text, None when it succeeded. Never raises, so a download that
        nobody waits for any more cannot end as an unretrieved task exception."""
        log.info("Preparing the runner: downloading %s (about 2.8 GB, once).", self.config.image)
        try:
            await self.docker.pull_image(self.config.image)
        except DockerError as error:
            log.warning("The download of %s failed: %s", self.config.image, error)
            return str(error)
        log.info("The runner image %s is downloaded.", self.config.image)
        return None

    async def pulled_image(self) -> dict[str, Any] | None:
        """Download the missing image once, however many starts need it: they all wait for the same download, up to
        `pull_wait`; the download itself goes on after that. Only the configured name is ever pulled."""
        image = self.config.image
        if is_local_name(image):
            raise SandboxError(
                503, f"The sandbox image {image} is built on this computer and is not there; build it with "
                "docker compose up -d --build."
            )
        if self.pull is None or self.pull.done():
            self.pull = asyncio.ensure_future(self.download_image())
        finished, _ = await asyncio.wait({self.pull}, timeout=self.config.pull_wait)
        if not finished:
            raise SandboxError(
                503, "Preparing the runner (about 2.8 GB, once): the download goes on; try again in a few minutes."
            )
        if (failure := self.pull.result()) is not None:
            raise SandboxError(503, f"The sandbox image {image} could not be downloaded: {failure}")
        return await self.docker.inspect_image(image)

    async def check_image(self) -> str:
        """The sandbox image's id, after downloading it when it is missing; refuses an image without the image
        label, also one that was just downloaded."""
        image = await self.docker.inspect_image(self.config.image)
        if image is None:
            image = await self.pulled_image()
        if image is None:
            raise SandboxError(503, f"The sandbox image {self.config.image} is not there after its download.")
        labels = (image.get("Config") or {}).get("Labels") or {}
        if labels.get(IMAGE_LABEL) != "1":
            raise SandboxError(403, f"The image {self.config.image} is not YAAP's sandbox image.")
        return str(image.get("Id") or "")

    async def image_id(self) -> str | None:
        """The id of the sandbox image when it is there, for comparing with a container's config."""
        image = await self.docker.inspect_image(self.config.image)
        return str(image.get("Id") or "") if image is not None else None

    def container_body(self, key: str, spec: StartSpec, host_folder: str, image_id: str = "") -> dict[str, Any]:
        host_key = base64.b64encode(spec.host_key.encode()).decode()
        return {
            "Image": self.config.image,
            "Hostname": "sandbox",
            "Env": [f"YAAP_AUTHORIZED_KEY={spec.authorized_key.strip()}", f"YAAP_HOST_KEY={host_key}"],
            "Labels": {
                LABEL: "1",
                KEY_LABEL: key,
                INSTANCE_LABEL: self.instance,
                CONFIG_LABEL: spec.config_hash(self.config.image, image_id),
                NETWORK_LABEL: "on" if spec.network else "off",
            },
            "ExposedPorts": {f"{SSH_PORT}/tcp": {}},
            "HostConfig": {
                "Mounts": [{"Type": "bind", "Source": host_folder, "Target": WORKSPACE}],
                "NetworkMode": self.config.network_on if spec.network else self.config.network_off,
                "NanoCpus": int(self.config.cpus * 1e9),
                "Memory": self.config.memory,
                "PidsLimit": self.config.pids,
                "RestartPolicy": {"Name": "no"},
                "Init": True,
                "CapDrop": ["NET_RAW", "MKNOD"],
                "PortBindings": {},
                "Privileged": False,
            },
        }

    async def start(self, key: str, spec: StartSpec) -> dict[str, Any]:
        """Make sure the sandbox runs as asked: start it, or create it first when it is missing or no longer fits
        (another folder, network or key). `created` tells YAAP to pin the host key it sent."""
        spec.check()
        async with self.lock(key):
            name = self.name(key)
            info = await self.find(key)
            image_id = await self.image_id()
            # Without the image an existing container is kept as it is: it could not be made again.
            if image_id is not None and info is not None:
                if info["Config"]["Labels"].get(CONFIG_LABEL) != spec.config_hash(self.config.image, image_id):
                    await self.docker.remove_container(name)
                    info = None
            created = False
            if info is None:
                host_folder = self.host_folder(spec.folder)
                image_id = await self.check_image()
                await self.docker.create_container(name, self.container_body(key, spec, host_folder, image_id))
                created = True
            if info is None or not info["State"].get("Running"):
                await self.docker.start_container(name)
            return {"key": key, "state": "running", "created": created}

    async def stop(self, key: str) -> dict[str, Any]:
        async with self.lock(key):
            info = await self.find(key)
            if info is None:
                return {"key": key, "state": "missing"}
            if info["State"].get("Running"):
                await self.docker.stop_container(self.name(key))
            return {"key": key, "state": "exited"}

    async def remove(self, key: str) -> dict[str, Any]:
        async with self.lock(key):
            if await self.find(key) is not None:
                await self.docker.remove_container(self.name(key))
            return {"key": key, "state": "missing"}

    async def status(self, key: str) -> dict[str, Any]:
        info = await self.find(key)
        if info is None:
            return {"key": key, "state": "missing"}
        state = info.get("State") or {}
        labels = info["Config"]["Labels"]
        found: dict[str, Any] = {
            "key": key,
            "state": state.get("Status", "unknown"),
            "network": labels.get(NETWORK_LABEL) == "on",
            "started_at": state.get("StartedAt"),
            "image": info["Config"].get("Image"),
            "cpu_percent": None,
            "memory_bytes": None,
            "memory_limit": None,
        }
        if state.get("Running"):
            try:
                stats = await self.docker.stats(self.name(key))
            except DockerError:
                return found
            found["cpu_percent"] = cpu_percent(stats)
            found["memory_bytes"], found["memory_limit"] = memory_used(stats)
        return found

    async def list_all(self) -> list[dict[str, Any]]:
        containers = await self.docker.list_containers([f"{LABEL}=1", f"{INSTANCE_LABEL}={self.instance}"])
        found = []
        for container in containers:
            labels = container.get("Labels") or {}
            if labels.get(LABEL) == "1" and labels.get(INSTANCE_LABEL) == self.instance and labels.get(KEY_LABEL):
                found.append({"key": labels[KEY_LABEL], "state": container.get("State", "unknown")})
        return found

    async def address(self, key: str) -> tuple[str, int]:
        """Where the relay reaches a running sandbox's sshd: its name on the sandbox networks."""
        info = await self.find(key)
        if info is None or not info["State"].get("Running"):
            raise SandboxError(409, f"The sandbox {key} is not running.")
        return self.name(key), SSH_PORT
