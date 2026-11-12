"""The runner of an environment: where the agent's commands run.

A runner is a sandbox container of the environment instead of one project, started by the same yaap-sandbox helper.
Its key is `e<id>`; it mounts only the environment's folder `/data/workspace/<environment>` as `/workspace`, so a
command in one environment cannot read another environment's projects, YAAP's database, the master key or the Claude
Code sign-in. YAAP shares no network with it. It is the default place for the commands of every project in the
environment that has no target or sandbox of its own:

- `run_command`, `start_process` and shell sessions run there over SSH (as on a sandbox, through the helper's relay);
- MCP servers over stdio and the agent's browser (Playwright MCP) run there too (app/mcp_client/remote.py);
- the file tools, checkpoints, the git tools, semantic search, the editor, Claude Code and Codex stay in YAAP, on the
  same project folder.

Like a sandbox it is a row in `targets` (kind runner, no project) with its own ed25519 key in the vault and a host key
YAAP made and pinned; it starts on first use and stops after the sandbox idle time. Unlike a project sandbox it is
online by default (the environment setting runner_network). When the runner cannot be started, commands are refused
with what to do; YAAP never falls back to running them in its own container. `YAAP_RUNNER=off` runs them in YAAP's
process instead, for development without Docker and for the test suite.
"""

import socket
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import anyio
import asyncssh
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import environment_value
from app.core.settings import SETTING_DEFINITIONS, resolve_setting
from app.credentials.crypto import CredentialCipher
from app.sandbox.client import SandboxUnavailable
from app.sandbox.service import (
    NO_DOCKER,
    RUNNER,
    SANDBOX_ROOT,
    SANDBOXES,
    SSH_KEY,
    SandboxManager,
    SandboxSpec,
    SandboxTarget,
    connection_key,
    public_key,
    runner_key,
)
from app.storage.models import Environment, Target
from app.targets.base import TargetError
from app.targets.container import LocalFiles
from app.targets.keys import settings_for
from app.targets.ssh import SshExecutor, SshSettings, new_key_pair
from app.vault.store import save_entry

RUNNER_NAME = "Runner"
RUNNER_ROOT = SANDBOX_ROOT
UNAVAILABLE = (
    "Commands run in this environment's runner, which YAAP cannot start: {reason} YAAP does not run them in its own "
    "container. Start the runner with `docker compose up -d --build` in YAAP's folder (docs/install.md, \"The "
    "runner\"), then try again."
)


def runner_enabled() -> bool:
    """Whether commands run in runners; YAAP_RUNNER=off runs them in YAAP's process (development, tests)."""
    return environment_value("YAAP_RUNNER", "on").strip().lower() not in ("off", "0", "false", "no")


class RunnerUnavailable(TargetError):
    """The runner cannot be started (no helper, no image); the message says what to do."""


def is_runner(target: Target | None) -> bool:
    return target is not None and target.kind == RUNNER


def find_runner(session: Session, environment_id: int) -> Target | None:
    return session.scalar(
        select(Target).where(Target.kind == RUNNER, Target.environment_id == environment_id).order_by(Target.id)
    )


def runner_network(session: Session, environment_id: int) -> bool:
    return resolve_setting(session, SETTING_DEFINITIONS["runner_network"], environment_id).value != "off"


def runner_path(environment_dir: Path, local: str) -> str:
    """Where a folder of the environment in YAAP's workspace is in the runner: /data/workspace/work/site is
    /workspace/site there. Raises TargetError for a folder outside the environment."""
    try:
        relative = Path(local).resolve().relative_to(environment_dir.resolve())
    except ValueError as error:
        raise TargetError(f"{local} is not inside this environment's folder, so it is not in its runner.") from error
    parts = relative.as_posix()
    return RUNNER_ROOT if parts in ("", ".") else str(PurePosixPath(RUNNER_ROOT) / parts)


def describe_runner(target: Target, project_folder: str) -> str:
    """The part of the system prompt that says where commands run."""
    network = (
        "It has network access, so you can install packages."
        if target.network
        else "It has no network access: installing packages from the internet fails; the user can switch it on."
    )
    return (
        "Commands (run_command, start_process) run in this environment's runner: a Debian container that holds only "
        f"this environment's projects, under {RUNNER_ROOT}. A command starts in the project folder, which is "
        f"{project_folder} there; the file tools work on the same folder. Commands run in bash as the user yaap, who "
        f"may use sudo inside the runner. {network} Packages installed outside {RUNNER_ROOT} last until the runner is "
        "rebuilt. The browser runs in the runner too, so localhost there is a dev server you started. " + NO_DOCKER
    )


class RunnerTarget(SandboxTarget):
    """The runner as the run's target: commands over SSH in the runner, files on YAAP's own disk.

    The run passes YAAP's path of the project folder as the root (it has no target_root), so the file tools stay
    local and the executor turns it into the folder's path in the runner.
    """

    kind = RUNNER

    def __init__(
        self,
        key: str,
        settings: SshSettings,
        manager: SandboxManager,
        target_id: int,
        spec: SandboxSpec,
        environment_dir: Path,
    ) -> None:
        super().__init__(key, settings, manager, target_id, spec)
        self.environment_dir = environment_dir

    async def connection(self) -> asyncssh.SSHClientConnection:
        try:
            return await super().connection()
        except SandboxUnavailable as error:
            raise RunnerUnavailable(UNAVAILABLE.format(reason=str(error).rstrip(".") + ".")) from error

    def files(self, root: str) -> LocalFiles:
        return LocalFiles(root)

    def executor(self, root: str) -> SshExecutor:
        return SshExecutor(self, runner_path(self.environment_dir, root))


@dataclass
class Runners:
    """The runners of this YAAP. Bound to the database, the vault and the workspace when YAAP starts (main.py)."""

    enabled: bool = True
    manager: SandboxManager = SANDBOXES
    session_factory: sessionmaker[Session] | None = None
    cipher: CredentialCipher | None = None
    workspace_dir: Path | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def bind(self, session_factory: sessionmaker[Session], cipher: CredentialCipher, workspace_dir: Path) -> None:
        self.session_factory = session_factory
        self.cipher = cipher
        self.workspace_dir = workspace_dir
        self.enabled = runner_enabled()

    def runner_row(self, session: Session, environment_id: int) -> Target:
        """The environment's runner row, made with its key on first use and then committed with the session (as
        reading the key from the vault commits it too); the lock keeps two first replies from making two."""
        if self.cipher is None:
            raise RunnerUnavailable(UNAVAILABLE.format(reason="YAAP has not started its runners yet."))
        with self.lock:
            found = find_runner(session, environment_id)
            if found is None:
                found = new_runner(session, self.cipher, session.get_one(Environment, environment_id))
                session.commit()
            return found

    def ensure(self, environment_id: int) -> int:
        """The id of the environment's runner row, in a session of its own."""
        if self.session_factory is None:
            raise RunnerUnavailable(UNAVAILABLE.format(reason="YAAP has not started its runners yet."))
        with self.session_factory() as session:
            return self.runner_row(session, environment_id).id

    def target(
        self, session: Session, environment_id: int, used_by: str, conversation_id: int | None = None
    ) -> tuple[Target, RunnerTarget]:
        """The environment's runner row and its runtime. Starting it waits for the first command, so a reply in an
        environment whose runner cannot start still begins; its commands are refused then."""
        if self.cipher is None or self.workspace_dir is None:
            raise RunnerUnavailable(UNAVAILABLE.format(reason="YAAP has not started its runners yet."))
        target = self.runner_row(session, environment_id)
        environment = session.get_one(Environment, environment_id)
        environment_dir = self.workspace_dir / environment.slug
        environment_dir.mkdir(parents=True, exist_ok=True)
        target.network = runner_network(session, environment_id)
        settings = settings_for(session, self.cipher, target, used_by, conversation_id)
        key = runner_key(environment_id)
        manager = self.manager

        async def open_socket() -> socket.socket:
            if manager.helper is None:
                raise SandboxUnavailable("the sandbox helper is not configured (YAAP_SANDBOX_URL).")
            return await manager.helper.open_socket(key)

        settings.extra["open_socket"] = open_socket
        spec = SandboxSpec(key, environment.slug, bool(target.network), public_key(session, target))
        runtime = RunnerTarget(connection_key(target), settings, manager, target.id, spec, environment_dir)
        return target, runtime

    def runtime(self, environment_id: int, used_by: str) -> RunnerTarget:
        """The runtime of the environment's runner, built in a session of its own. Synchronous (it reads the
        database and the vault): call it from a worker thread."""
        if self.session_factory is None:
            raise RunnerUnavailable(UNAVAILABLE.format(reason="YAAP has not started its runners yet."))
        with self.session_factory() as session:
            return self.target(session, environment_id, used_by)[1]

    async def place(self, environment_id: int, used_by: str) -> RunnerTarget:
        """The runtime of the environment's runner, for MCP servers and the browser; built in a worker thread."""
        return await anyio.to_thread.run_sync(self.runtime, environment_id, used_by)


def new_runner(session: Session, cipher: CredentialCipher, environment: Environment) -> Target:
    """The environment's runner row with its own key in the vault, never offered to the agent. Does not commit."""
    now = datetime.now(UTC)
    private_key, public = new_key_pair(f"yaap-runner-{environment.slug}")
    entry = save_entry(
        session,
        cipher,
        environment_id=environment.id,
        project_id=None,
        name=f"runner {environment.id} {environment.slug}"[:64],
        kind=SSH_KEY,
        value=private_key,
        agent_use=False,
        description=f"The key YAAP signs in with on the runner of the environment {environment.name}.",
    )
    entry.public_fields = {"public_key": public}
    entry.last_four = ""
    target = Target(
        environment_id=environment.id,
        name=RUNNER_NAME,
        host=RUNNER,
        port=22,
        user="yaap",
        system="linux",
        shell="bash",
        rights="standard",
        folders=[RUNNER_ROOT],
        allow_full_access=True,
        whole_computer=False,
        kind=RUNNER,
        project_id=None,
        network=True,
        created_at=now,
        updated_at=now,
    )
    session.add(target)
    session.flush()
    target.key_entry_id = entry.id
    return target


RUNNERS = Runners()
