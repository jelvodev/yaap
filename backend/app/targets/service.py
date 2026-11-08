"""Choosing and preparing a target for a run.

A project sets its target and the folder on it (settings target and target_folder); a conversation outside a project
picks a target before its first reply. On a target the agent works in that folder, or for a conversation outside a
project in the target's first listed folder, or on the whole disk when the target's whole_computer switch is on.
"""

import posixpath
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.agent.policy import AccessLevel
from app.core.settings import SETTING_DEFINITIONS, resolve_setting
from app.credentials.crypto import CredentialCipher
from app.sandbox.service import SANDBOX_ROOT, describe_sandbox, find_sandbox, is_sandbox, sandbox_run_target
from app.runner.service import RUNNERS, describe_runner, is_runner, runner_path
from app.storage.models import Conversation, Project, Target
from app.targets.base import TargetError
from app.targets.folders import WINDOWS_PATH, target_folder_problem
from app.targets.keys import settings_for
from app.targets.ssh import WINDOWS, SshSettings, SshTarget, connect, fingerprint, sftp_path

SSH_KEY = "ssh_key"
SYSTEM_NAMES = {"windows": "Windows", "linux": "Linux", "macos": "macOS"}
__all__ = ["settings_for", "target_folder_problem"]


def host_fingerprint(key: str | None) -> str | None:
    if not key:
        return None
    try:
        return fingerprint(key)
    except (ValueError, TypeError):
        return None


async def check_folders(settings: SshSettings, folders: list[str]) -> list[str]:
    """The listed folders that do not exist on the target."""
    if not folders:
        return []
    connection = await connect(settings)
    missing = []
    try:
        async with connection.start_sftp_client() as sftp:
            for folder in folders:
                if not await sftp.isdir(sftp_path(settings.system, folder)):
                    missing.append(folder)
    finally:
        connection.close()
    return missing


def inside(folder: str, allowed: str, system: str) -> bool:
    def norm(path: str) -> str:
        path = posixpath.normpath(path.replace("\\", "/"))
        return path.lower() if system == WINDOWS else path

    child, parent = norm(folder), norm(allowed)
    return child == parent or child.startswith(parent.rstrip("/") + "/")


def disk_root(target: Target, folder: str) -> str:
    if target.system == WINDOWS and WINDOWS_PATH.match(folder):
        return folder[:2] + "\\"
    return "/"


@dataclass
class RunTarget:
    target: Target
    runtime: SshTarget
    # The working folder as the target writes it.
    root: str

    @property
    def file_root(self) -> str | None:
        """The folder the file tools work in on the target; None when they work on YAAP's own disk (the runner)."""
        return None if is_runner(self.target) else self.root

    @property
    def has_container_tools(self) -> bool:
        """A sandbox and the runner work on the project folder in YAAP, so the run keeps the container's tools
        (checkpoints, git tools, semantic search); a computer over SSH gets the target's set."""
        return is_sandbox(self.target) or is_runner(self.target)


def working_folder(target: Target, project_folder: str | None) -> str:
    """The folder a run works in on the target; raises TargetError when it is not allowed."""
    folders = list(target.folders or [])
    if project_folder:
        if target.whole_computer or any(inside(project_folder, allowed, target.system) for allowed in folders):
            return project_folder
        raise TargetError(
            f"The project's folder {project_folder} is not one of the folders of the target {target.name}. Add it "
            "there, or switch on Whole computer."
        )
    if target.whole_computer:
        return disk_root(target, folders[0] if folders else "C:\\")
    if folders:
        return folders[0]
    raise TargetError(f"The target {target.name} has no folders. Add one in Settings › Targets.")


def chosen_target(session: Session, conversation: Conversation) -> tuple[Target, str | None] | None:
    """The target a conversation works on and, in a project, the project's folder there; None is YAAP's container.

    A project names its target in the setting target; a conversation outside a project chose one before its first
    reply. Raises TargetError when the chosen target is gone.
    """
    if conversation.project_id is not None:
        value = resolve_setting(
            session, SETTING_DEFINITIONS["target"], conversation.environment_id, conversation.project_id
        ).value
        if isinstance(value, dict) and value.get("sandbox"):
            # The project's own sandbox; the setting decides about the network.
            sandbox = find_sandbox(session, conversation.project_id)
            if sandbox is None:
                raise TargetError("This project's sandbox is not set up. Choose Sandbox again in the project settings.")
            sandbox.network = bool(value.get("network"))
            return sandbox, SANDBOX_ROOT
        if not isinstance(value, dict) or not value.get("id"):
            return None
        target_id, folder = int(value["id"]), str(value.get("folder") or "").strip() or None
    elif conversation.target_id is not None:
        target_id, folder = conversation.target_id, None
    else:
        return None
    target = session.get(Target, target_id)
    if target is None or target.environment_id != conversation.environment_id or is_sandbox(target):
        raise TargetError("The target of this conversation no longer exists. Choose another in the project settings.")
    return target, folder


def allowed_access(access: AccessLevel, target: Target | None, unattended: bool = False) -> AccessLevel:
    """The access a run gets: full on a target only when its Allow full access switch is on, never for a scheduled
    task; otherwise as set."""
    if is_runner(target):
        # The runner is where commands of a project in YAAP run: the same as YAAP's container used to be.
        return access
    if access == "full" and target is not None and (unattended or not target.allow_full_access):
        return "default"
    return access


def describe_target(target: Target, root: str) -> str:
    """The part of the system prompt that says where the agent works."""
    if is_sandbox(target):
        return describe_sandbox(target)
    if is_runner(target):
        return describe_runner(target, root)
    system = SYSTEM_NAMES.get(target.system, target.system)
    shell = {"pwsh": "PowerShell 7", "powershell": "Windows PowerShell"}.get(target.shell, target.shell)
    lines = [
        f"You work on the computer {target.name} ({system}) over SSH, not in YAAP's container. The file tools work in "
        f"the folder {root} there; paths are relative to it. Commands run in {shell} with that folder as the current "
        "folder.",
        "A change on this computer cannot be rolled back in YAAP: there are no checkpoints here. Prefer small, "
        "reversible steps, and say what you changed.",
    ]
    if target.system == WINDOWS:
        lines.append("Write commands for PowerShell, such as Get-ChildItem and $env:NAME, not for bash.")
    if target.whole_computer:
        lines.append("Whole computer is on for this computer: commands may reach any folder on it.")
    return "\n\n".join(lines)


def run_target(
    session: Session,
    cipher: CredentialCipher,
    target: Target,
    project_folder: str | None,
    conversation: Conversation,
) -> RunTarget:
    if is_sandbox(target):
        # Started, and its host key pinned, on first use.
        runtime = sandbox_run_target(session, cipher, target, conversation, f"Conversation in the sandbox of project {target.project_id}")
        return RunTarget(target, runtime, SANDBOX_ROOT)
    if not target.host_key:
        raise TargetError(f"The target {target.name} is not set up yet: test it in Settings › Targets first.")
    root = working_folder(target, project_folder)
    settings = settings_for(session, cipher, target, f"Conversation on {target.name}", conversation.id)
    return RunTarget(target, SshTarget(f"target-{target.id}", settings), root)


def conversation_run_target(session: Session, cipher: CredentialCipher, conversation: Conversation) -> RunTarget | None:
    """Where a conversation's run works: its project's target or sandbox, the computer it chose, or else, in a
    project, the environment's runner. None is YAAP's own container: a conversation outside a project
    without a target (it has no command tools), or every conversation when the runner is switched off.
    Raises TargetError when the chosen target is gone."""
    chosen = chosen_target(session, conversation)
    if chosen is not None:
        return run_target(session, cipher, chosen[0], chosen[1], conversation)
    if conversation.project_id is None or not RUNNERS.enabled or RUNNERS.workspace_dir is None:
        return None
    project = session.get(Project, conversation.project_id)
    if project is None:
        return None
    target, runtime = RUNNERS.target(
        session, conversation.environment_id, f"Conversation in the runner of {project.name}", conversation.id
    )
    root = runner_path(runtime.environment_dir, str(RUNNERS.workspace_dir / project.folder))
    return RunTarget(target, runtime, root)
