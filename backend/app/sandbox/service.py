"""A project's sandbox as a target.

A project that chooses *Sandbox* under *Where the agent works* (the setting target is {"sandbox": true, "network":
false}) gets a row in `targets` of kind sandbox, with its own ed25519 key in the vault like any target. It is an SSH
target, so files, commands, processes and shell sessions work on it without new code; what differs:

- The container is started by the yaap-sandbox helper on first use (`SandboxTarget.connection`) and stopped after an
  hour without use. YAAP sends a host key it made with every start; when the helper had to create the container it
  answers `created`, and YAAP pins that key: nobody needs to confirm it, because YAAP made it.
- The connection goes through the helper's relay (app/sandbox/client.py), not to a host and port.
- The project folder in YAAP's container is the sandbox's /workspace, so checkpoints and the git tools keep working
  on it, and changing tools follow the project's permissions as in the container, rather than asking every time as on
  a computer YAAP cannot roll back (app/agent/tools.py).
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.credentials.crypto import CredentialCipher
from app.sandbox.client import NOT_ENABLED, HelperClient, SandboxUnavailable
from app.storage.models import Conversation, Project, Target, VaultEntry
from app.targets.base import TargetError
from app.targets.keys import settings_for
from app.targets.ssh import CONNECTIONS, SshSettings, SshTarget, new_key_pair
from app.vault.store import remove_entry, save_entry

log = logging.getLogger(__name__)

SANDBOX = "sandbox"
SSH_KEY = "ssh_key"
SANDBOX_ROOT = "/workspace"
SANDBOX_NAME = "Sandbox"
SANDBOX_IDLE = timedelta(hours=1)
# Using a sandbox is written to the database at most this often.
USE_RECORDED_EVERY = timedelta(minutes=1)


def sandbox_key(project_id: int) -> str:
    return f"p{project_id}"


RUNNER = "runner"


def runner_key(environment_id: int) -> str:
    """The helper's key of an environment's runner (app/runner), a sandbox of the whole environment."""
    return f"e{environment_id}"


def container_key(target: Target) -> str | None:
    """The helper's key of a sandbox or runner row; None for a row that has no container of its own."""
    if target.kind == SANDBOX and target.project_id is not None:
        return sandbox_key(target.project_id)
    if target.kind == RUNNER:
        return runner_key(target.environment_id)
    return None


def connection_key(target: Target) -> str:
    return f"target-{target.id}"


def new_host_key() -> tuple[str, str]:
    """The host key for a new sandbox: the private key goes to the container, the public key is pinned."""
    return new_key_pair("yaap-sandbox-host")


def is_sandbox(target: Target | None) -> bool:
    return target is not None and target.kind == SANDBOX


def find_sandbox(session: Session, project_id: int) -> Target | None:
    return session.scalar(select(Target).where(Target.kind == SANDBOX, Target.project_id == project_id))


def public_key(session: Session, target: Target) -> str:
    entry = session.get(VaultEntry, target.key_entry_id) if target.key_entry_id else None
    return str((entry.public_fields or {}).get("public_key", "")) if entry else ""


def prepare_sandbox(session: Session, cipher: CredentialCipher, project: Project, network: bool) -> Target:
    """The project's sandbox target, made with its key on first use; network as asked. Does not commit."""
    now = datetime.now(UTC)
    target = find_sandbox(session, project.id)
    if target is None:
        private_key, public = new_key_pair(f"yaap-sandbox-{project.slug}")
        entry = save_entry(
            session,
            cipher,
            environment_id=project.environment_id,
            project_id=None,
            name=f"sandbox {project.id} {project.slug}"[:64],
            kind=SSH_KEY,
            value=private_key,
            agent_use=False,
            description=f"The key YAAP signs in with on the sandbox of the project {project.name}.",
        )
        entry.public_fields = {"public_key": public}
        entry.last_four = ""
        target = Target(
            environment_id=project.environment_id,
            name=SANDBOX_NAME,
            host=SANDBOX,
            port=22,
            user="yaap",
            system="linux",
            shell="bash",
            rights="standard",
            folders=[SANDBOX_ROOT],
            # The sandbox is the place made for working without asking: full access needs no extra switch.
            allow_full_access=True,
            whole_computer=False,
            kind=SANDBOX,
            project_id=project.id,
            network=network,
            created_at=now,
            updated_at=now,
        )
        session.add(target)
        session.flush()
        target.key_entry_id = entry.id
    elif target.network != network or target.environment_id != project.environment_id:
        target.network = network
        target.environment_id = project.environment_id
        target.updated_at = now
    return target


# Said in the runner's and the sandbox's part of the system prompt: agents otherwise try to install and start Docker.
NO_DOCKER = (
    "There is no Docker inside: no dockerd and no docker compose, as the container is unprivileged. Run a web app "
    "with start_process and its port; YAAP shows it in the preview beside the chat. A Dockerfile or compose file can "
    "still be written for the user to run on their own computer."
)


def describe_sandbox(target: Target) -> str:
    """The part of the system prompt that says where the agent works."""
    network = (
        "It has network access, so you can install packages and fetch dependencies."
        if target.network
        else "It has no network access: installing packages or fetching dependencies from the internet fails. Say "
        "so when a task needs it; the user can switch the network on for this project."
    )
    return "\n\n".join(
        [
            "You work in this project's sandbox: a Debian container of its own, reached over SSH, not YAAP's "
            f"container. The project folder is {SANDBOX_ROOT} there; the file tools work in it and paths are relative "
            "to it. Commands run in bash as the user yaap, who may use sudo inside the sandbox (for example sudo "
            "apt-get install). Build tools, Python 3 with venv and pip, Node.js with npm, and git are installed.",
            network,
            f"Changes in {SANDBOX_ROOT} are the project's files and have checkpoints as usual. Anything outside it, such "
            "as installed packages, lasts until the sandbox is rebuilt; it stops after an hour without use.",
            NO_DOCKER,
        ]
    )


@dataclass
class SandboxSpec:
    key: str
    folder: str
    network: bool
    authorized_key: str


class SandboxTarget(SshTarget):
    """An SSH target that starts its container before connecting and records every use for the idle stop."""

    kind = SANDBOX

    def __init__(self, key: str, settings: SshSettings, manager: "SandboxManager", target_id: int, spec: SandboxSpec) -> None:
        super().__init__(key, settings)
        self.manager = manager
        self.target_id = target_id
        self.spec = spec

    def note_use(self) -> None:
        """A use that does not open a connection (a call to an MCP server running in it) also keeps it up."""
        self.manager.touch(self.target_id)

    async def connection(self) -> Any:
        self.manager.touch(self.target_id)
        if not self.is_open():
            async with self.manager.lock(self.target_id):
                if not self.is_open():
                    await self.manager.start(self.target_id, self.spec, self.settings)
        return await CONNECTIONS.get(self.key, self.settings)


class SandboxManager:
    """The sandboxes of this YAAP: starting them through the helper, recording use, stopping idle ones.

    Bound to the database and the helper when YAAP starts (main.py); tests give it a helper of their own.
    """

    def __init__(self) -> None:
        self.helper: HelperClient | None = None
        self.session_factory: sessionmaker[Session] | None = None
        # The background processes, so a sandbox or runner with a running dev server is not stopped as idle.
        self.processes: Any = None
        self.recorded: dict[int, datetime] = {}
        self.locks: dict[tuple[int, int], asyncio.Lock] = {}

    def bind(self, session_factory: sessionmaker[Session], helper: HelperClient | None, processes: Any = None) -> None:
        self.session_factory = session_factory
        self.helper = helper
        self.processes = processes
        self.recorded.clear()

    def client(self) -> HelperClient:
        if self.helper is None:
            raise SandboxUnavailable(NOT_ENABLED)
        return self.helper

    def lock(self, target_id: int) -> asyncio.Lock:
        # One lock per sandbox and event loop (tests run several loops one after another).
        return self.locks.setdefault((target_id, id(asyncio.get_running_loop())), asyncio.Lock())

    def touch(self, target_id: int) -> None:
        now = datetime.now(UTC)
        last = self.recorded.get(target_id)
        if last is not None and now - last < USE_RECORDED_EVERY:
            return
        self.recorded[target_id] = now
        if self.session_factory is None:
            return
        with self.session_factory() as session:
            target = session.get(Target, target_id)
            if target is not None:
                target.last_used_at = now
                session.commit()

    def pin(self, target_id: int, host_key: str) -> None:
        if self.session_factory is None:
            return
        with self.session_factory() as session:
            target = session.get(Target, target_id)
            if target is not None:
                target.host_key, target.pending_host_key = host_key, None
                target.last_tested_at, target.last_test_result = datetime.now(UTC), "Sandbox created."
                session.commit()

    async def start(self, target_id: int, spec: SandboxSpec, settings: SshSettings) -> None:
        """Start the sandbox, creating it when needed; a new container's host key is pinned at once."""
        helper = self.client()
        private_key, public = new_host_key()
        answer = await helper.start(spec.key, spec.folder, spec.network, spec.authorized_key, private_key)
        if not answer.get("created") and not settings.host_key:
            # A container YAAP holds no pin for (a restored database, say): never trust it, make a new one.
            await helper.remove(spec.key)
            private_key, public = new_host_key()
            answer = await helper.start(spec.key, spec.folder, spec.network, spec.authorized_key, private_key)
        if answer.get("created"):
            CONNECTIONS.close(f"target-{target_id}")
            settings.host_key = public
            self.pin(target_id, public)

    # --- Lifecycle ---------------------------------------------------------------------------------

    async def stop(self, target: Target) -> None:
        CONNECTIONS.close(connection_key(target))
        key = container_key(target)
        if key is not None:
            await self.client().stop(key)

    async def rebuild(self, session: Session, cipher: CredentialCipher, target: Target) -> None:
        """Remove the container and start a new one from the current image; the project folder stays. Its new host
        key is pinned as for any new sandbox."""
        if target.project_id is None:
            raise TargetError("This sandbox belongs to no project.")
        CONNECTIONS.close(connection_key(target))
        await self.client().remove(sandbox_key(target.project_id))
        target.host_key = None
        session.commit()
        runtime = sandbox_run_target(session, cipher, target, None, f"Rebuild of the sandbox of project {target.project_id}")
        await runtime.connection()

    async def remove_container(self, project_id: int) -> None:
        """The container of a removed project. When the helper cannot be reached now, the idle pass removes it later
        as a sandbox no project owns."""
        if self.helper is None:
            return
        try:
            await self.helper.remove(sandbox_key(project_id))
        except SandboxUnavailable as error:
            log.warning("The sandbox of project %s was not removed yet: %s", project_id, error)

    def busy_keys(self) -> set[str]:
        """The containers a running background process keeps up: its project's sandbox, and its environment's runner
        (where the processes of projects without a place of their own run)."""
        if self.processes is None:
            return set()
        busy: set[str] = set()
        for process in list(self.processes.processes.values()):
            if process.status != "running":
                continue
            busy.add(runner_key(process.environment_id))
            if process.project_id is not None:
                busy.add(sandbox_key(process.project_id))
        return busy

    async def stop_idle(self) -> tuple[list[str], list[str]]:
        """Stop sandboxes and runners unused for an hour (none with a running background process), and remove
        containers of this YAAP that no project or environment owns any more. Returns the keys stopped and removed."""
        if self.helper is None or self.session_factory is None:
            return [], []
        try:
            listed = await self.helper.list_all()
        except SandboxUnavailable:
            return [], []
        now = datetime.now(UTC)
        busy = self.busy_keys()
        stopped: list[str] = []
        removed: list[str] = []
        with self.session_factory() as session:
            owned: dict[str, Target] = {}
            for target in session.scalars(select(Target).where(Target.kind.in_([SANDBOX, RUNNER]))):
                key = container_key(target)
                if key is not None:
                    owned.setdefault(key, target)
            for item in listed:
                key = str(item.get("key"))
                target = owned.get(key)
                if target is None:
                    removed.append(key)
                elif item.get("state") == "running" and key not in busy:
                    if target.last_used_at is None or now - target.last_used_at >= SANDBOX_IDLE:
                        CONNECTIONS.close(connection_key(target))
                        stopped.append(key)
        for key in stopped:
            try:
                await self.helper.stop(key)
            except SandboxUnavailable as error:
                log.warning("The idle sandbox %s was not stopped: %s", key, error)
        for key in removed:
            try:
                await self.helper.remove(key)
            except SandboxUnavailable as error:
                log.warning("The sandbox %s without a project was not removed: %s", key, error)
        return stopped, removed

    async def stop_idle_periodically(self, interval: float = 300) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await self.stop_idle()
            except Exception:  # noqa: BLE001 - the pass must keep running
                log.exception("The idle pass over the sandboxes failed.")


SANDBOXES = SandboxManager()


def sandbox_of(session: Session, project_id: int) -> tuple[int, int | None] | None:
    """The project's sandbox target and key entry, read before the project is removed."""
    target = find_sandbox(session, project_id)
    return (target.id, target.key_entry_id) if target is not None else None


async def remove_project_sandbox(session: Session, project_id: int, found: tuple[int, int | None] | None) -> None:
    """After a project is removed (its sandbox row went with it): its key in the vault and its container."""
    if found is None:
        return
    target_id, entry_id = found
    CONNECTIONS.close(f"target-{target_id}")
    entry = session.get(VaultEntry, entry_id) if entry_id else None
    if entry is not None:
        remove_entry(session, entry)
        session.commit()
    await SANDBOXES.remove_container(project_id)


def sandbox_run_target(
    session: Session, cipher: CredentialCipher, target: Target, conversation: Conversation | None, used_by: str
) -> SandboxTarget:
    """The runtime of a project's sandbox, for a reply (or the project panel)."""
    if SANDBOXES.helper is None:
        raise SandboxUnavailable(NOT_ENABLED)
    project = session.get(Project, target.project_id) if target.project_id else None
    if project is None:
        raise TargetError("This sandbox belongs to no project.")
    authorized = public_key(session, target)
    if not authorized:
        raise TargetError("The sandbox's key is gone from the vault; choose Sandbox again in the project settings.")
    settings = settings_for(session, cipher, target, used_by, conversation.id if conversation else None)
    key = sandbox_key(project.id)
    helper = SANDBOXES.helper
    settings.extra["open_socket"] = lambda: helper.open_socket(key)
    spec = SandboxSpec(key, project.folder, bool(target.network), authorized)
    return SandboxTarget(connection_key(target), settings, SANDBOXES, target.id, spec)
