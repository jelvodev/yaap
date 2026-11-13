"""Where a tool call works: the run's project folder, its files and its commands, in the container or over SSH.

Shared by the tool modules and the gate, so none of them imports another for these.
"""

import stat
from pathlib import Path

from app.agent.runs import AgentRun
from app.agent.toolkit import ToolError
from app.targets.base import CommandExecutor, TargetFiles
from app.targets.container import CONTAINER, LocalFiles


def project_root(run: AgentRun) -> Path:
    if run.project_folder is None:
        raise ToolError("This conversation has no project folder.")
    try:
        info = run.project_folder.lstat()
    except OSError as error:
        raise ToolError("The project folder is unavailable or was replaced.") from error
    if (
        not stat.S_ISDIR(info.st_mode)
        or run.project_identity is None
        or (info.st_dev, info.st_ino) != run.project_identity
    ):
        raise ToolError("The project folder was replaced while this run was active.")
    return run.project_folder


def files(run: AgentRun) -> TargetFiles:
    """The run's working folder: on a computer over SSH the folder there, else the project folder in the container."""
    if run.target is not None and run.target_root is not None:
        return run.target.files(run.target_root)
    root = str(project_root(run))
    if run.target is None or in_runner(run):
        # Files on YAAP's disk: the folder opened must be the one the run pinned, so a swap after the check above
        # is refused instead of followed.
        return LocalFiles(root, run.project_identity)
    return run.target.files(root)


def executor(run: AgentRun) -> CommandExecutor:
    if run.target is not None and run.target_root is not None:
        return run.target.executor(run.target_root)
    return (run.target or CONTAINER).executor(str(project_root(run)))


def on_target(run: AgentRun) -> bool:
    """Whether the run works on a computer over SSH rather than in YAAP's container."""
    return run.target is not None and run.target_root is not None


def in_sandbox(run: AgentRun) -> bool:
    """Whether the run works in its project's sandbox: over SSH, but on the project folder in YAAP, so checkpoints
    work and changes follow the project's permissions as in the container."""
    return getattr(run.target, "kind", "") == "sandbox"


def in_runner(run: AgentRun) -> bool:
    """Whether the run's commands go to its environment's runner: files stay on YAAP's disk, commands, processes and
    shell sessions run in the runner over SSH."""
    return getattr(run.target, "kind", "") == "runner"


def commands_remote(run: AgentRun) -> bool:
    """Whether commands run over SSH (a computer, a sandbox or the runner) rather than in YAAP's own process."""
    return on_target(run) or in_runner(run)


def target_label(run: AgentRun) -> str:
    return str(getattr(getattr(run.target, "settings", None), "label", "") or "this computer")
