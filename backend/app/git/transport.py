"""The transport repository: where the GitHub token is used, and the only place it is used.

A project folder in the workspace can be written by the runner, including its `.git/config`, so a git command that
carries the token must never read that configuration (an `http.<url>.proxy` with `sslVerify=false` would hand the
token over). Each work repository therefore has a bare transport repository that only YAAP writes, in YAAP's data
folder outside what any runner mounts. Fetch, push and clone talk to the remote from there; objects travel between
the transport and the work repository as git bundles (`app.git.bundles`).

`network_step` is the one decision of where a step runs, and `Transport.run` dispatches through it.
"""

import hashlib
import os
import shutil
import stat
import tempfile
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.git.common import DEFAULT_BRANCH, GIT_TIMEOUT_SECONDS, GitError, GitResult
from app.git.safety import RECEIVE_PACK, UPLOAD_PACK, ConfigEntry
from app.git.service import Repository, run_git
from app.runner.service import RUNNERS, Runners

TransportStep = Literal[
    "remote_fetch",
    "remote_push",
    "remote_clone",
    "transport_init",
    "transport_read",
    "transport_bundle",
    "transport_unbundle",
    "transport_update_ref",
    "work_init",
    "work_read",
    "work_bundle",
    "work_unbundle",
    "work_update_tracking",
    "work_set_upstream",
    "work_merge",
    "work_checkout",
]
# "transport": YAAP's own process, in the transport repository. "work": in the work repository's place, which is the
# runner for a project in the workspace.
StepPlace = Literal["transport", "work"]

STEP_PLACES: dict[TransportStep, StepPlace] = {
    "remote_fetch": "transport",
    "remote_push": "transport",
    "remote_clone": "transport",
    "transport_init": "transport",
    "transport_read": "transport",
    "transport_bundle": "transport",
    "transport_unbundle": "transport",
    "transport_update_ref": "transport",
    "work_init": "work",
    "work_read": "work",
    "work_bundle": "work",
    "work_unbundle": "work",
    "work_update_tracking": "work",
    "work_set_upstream": "work",
    "work_merge": "work",
    "work_checkout": "work",
}
TOKEN_STEPS: frozenset[TransportStep] = frozenset({"remote_fetch", "remote_push", "remote_clone"})
# These make the transport itself, so they start in the folder that holds the transports.
CREATING_STEPS: frozenset[TransportStep] = frozenset({"transport_init", "remote_clone"})
TRANSPORT_FOLDER = "git-transport"
DELIVERED_MARKER = "yaap-delivered"
NETWORK_TIMEOUT = 300
CLONE_TIMEOUT = 600
BRANCH_REFSPEC = "+refs/heads/*:refs/heads/*"


def network_step(step: TransportStep) -> StepPlace:
    """Where a step runs."""
    return STEP_PLACES[step]


def carries_token(step: TransportStep) -> bool:
    """Whether the step talks to the remote and so gets the GitHub token."""
    return step in TOKEN_STEPS


def transport_path(folder: Path, runners: Runners = RUNNERS) -> Path:
    """Where the transport of a work repository is: next to the workspace, never inside it."""
    if runners.workspace_dir is None:
        raise GitError("YAAP has not started yet, so it has no place for the git transport.")
    # Named by the path as written, not with links followed: a project folder the runner replaced by a link to another
    # project must not share that project's transport.
    key = hashlib.sha256(os.path.abspath(folder).encode("utf-8")).hexdigest()[:32]
    return runners.workspace_dir.parent / TRANSPORT_FOLDER / f"{key}.git"


_locks: dict[Path, threading.Lock] = {}
_locks_guard = threading.Lock()


def transport_lock(path: Path) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(path, threading.Lock())


@dataclass(frozen=True)
class Transport:
    """A transport repository and the place of the work repository it serves."""

    path: Path
    place: Repository

    def run(
        self,
        step: TransportStep,
        arguments: Sequence[str],
        *,
        authorization: Sequence[ConfigEntry] = (),
        config: Sequence[str] = (),
        timeout: int = GIT_TIMEOUT_SECONDS,
        accept_exit_codes: Sequence[int] = (0,),
    ) -> GitResult:
        """Run one git step where `network_step` says. The token goes only to a step that carries it."""
        if authorization and not carries_token(step):
            raise GitError(f"The git step {step} does not talk to the remote, so it gets no token.")
        if network_step(step) == "work":
            return run_git(arguments, self.place, config=config, timeout=timeout, accept_exit_codes=accept_exit_codes)
        if step in CREATING_STEPS:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            return run_git(
                arguments,
                self.path.parent,
                config=config,
                timeout=timeout,
                accept_exit_codes=accept_exit_codes,
                private_config=authorization,
            )
        # GIT_DIR names the repository because git is set to use a bare repository only when told which one.
        return run_git(
            arguments,
            self.path,
            extra_environment={"GIT_DIR": str(self.path)},
            config=config,
            timeout=timeout,
            accept_exit_codes=accept_exit_codes,
            private_config=authorization,
        )

    def exists(self) -> bool:
        return (self.path / "HEAD").is_file()

    def ensure(self) -> None:
        if not self.exists():
            self.run("transport_init", ["init", "--bare", "-b", DEFAULT_BRANCH, str(self.path)])


@contextmanager
def open_transport(
    folder: Path, place: Repository, runners: Runners = RUNNERS, fresh: bool = False
) -> Iterator[Transport]:
    """The transport of a work repository, made when missing, held exclusively: a periodic fetch and a push by the
    user may overlap. `fresh` removes an existing one first (a clone starts clean)."""
    path = transport_path(folder, runners)
    with transport_lock(path):
        if fresh:
            remove_transport(path)
        transport = Transport(path, place)
        if not fresh:
            transport.ensure()
        yield transport


def remove_transport(path: Path) -> None:
    def make_writable(function: Callable[[str], object], target: str, _error: BaseException) -> None:
        os.chmod(target, stat.S_IWRITE)  # git leaves object files read-only, which blocks removing them on Windows
        function(target)

    if path.exists():
        shutil.rmtree(path, onexc=make_writable)


# --- Steps that talk to the remote ---------------------------------------------------------------------------------


def remote_fetch(transport: Transport, url: str, authorization: Sequence[ConfigEntry]) -> None:
    """Every branch of the remote into the transport; branches the remote no longer has are removed."""
    arguments = ["fetch", UPLOAD_PACK, "--prune", "--no-tags", url, BRANCH_REFSPEC]
    transport.run("remote_fetch", arguments, authorization=authorization, timeout=NETWORK_TIMEOUT)


def remote_push(transport: Transport, url: str, authorization: Sequence[ConfigEntry], sha: str, branch: str) -> None:
    """One commit to one branch of the remote, never forced."""
    arguments = ["push", RECEIVE_PACK, url, f"{sha}:refs/heads/{branch}"]
    transport.run("remote_push", arguments, authorization=authorization, timeout=NETWORK_TIMEOUT)


def remote_clone(transport: Transport, url: str, authorization: Sequence[ConfigEntry]) -> None:
    """A bare clone as the new transport (its HEAD is the remote's default branch)."""
    arguments = ["clone", "--bare", UPLOAD_PACK, url, str(transport.path)]
    transport.run("remote_clone", arguments, authorization=authorization, timeout=CLONE_TIMEOUT)


# --- Steps inside the transport ------------------------------------------------------------------------------------


def branch_tips(transport: Transport) -> tuple[tuple[str, str], ...]:
    """(commit, ref) of every branch in the transport."""
    output = transport.run(
        "transport_read", ["for-each-ref", "--format=%(objectname) %(refname)", "refs/heads/"]
    ).stdout
    return tuple((line.split(" ", 1)[0], line.split(" ", 1)[1]) for line in output.splitlines() if " " in line)


def refs_digest(transport: Transport) -> str:
    return "\n".join(f"{sha} {ref}" for sha, ref in branch_tips(transport))


def read_delivered(transport: Transport) -> str | None:
    marker = transport.path / DELIVERED_MARKER
    return marker.read_text(encoding="utf-8") if marker.is_file() else None


def write_delivered(transport: Transport) -> None:
    """Remember which branches the work repository has, so a delivery that failed is tried again."""
    (transport.path / DELIVERED_MARKER).write_text(refs_digest(transport), encoding="utf-8")


def default_branch(transport: Transport) -> str | None:
    """The remote's default branch as the transport saw it at the clone, or None when it has none."""
    result = transport.run(
        "transport_read", ["symbolic-ref", "--quiet", "HEAD"], accept_exit_codes=(0, 1)
    ).stdout.strip()
    return result.removeprefix("refs/heads/") if result.startswith("refs/heads/") else None


def branch_tip(transport: Transport, branch: str) -> str | None:
    found = transport.run(
        "transport_read", ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], accept_exit_codes=(0, 1)
    )
    return found.stdout.strip() or None


def bundle_all_branches(transport: Transport) -> bytes | None:
    """Every branch of the transport as one bundle; None when it has none (git refuses an empty bundle)."""
    if not branch_tips(transport):
        return None
    with tempfile.TemporaryDirectory(prefix="yaap-bundle-") as folder:
        target = Path(folder) / "branches.bundle"
        transport.run("transport_bundle", ["bundle", "create", str(target), "--branches"], timeout=NETWORK_TIMEOUT)
        return target.read_bytes()


def unbundle_into_transport(transport: Transport, data: bytes) -> str:
    """Take the objects of a bundle into the transport; returns git's list of the refs the bundle holds. Objects are
    checked, because the bytes come from a place a runner can influence."""
    with tempfile.TemporaryDirectory(prefix="yaap-bundle-") as folder:
        source = Path(folder) / "incoming.bundle"
        source.write_bytes(data)
        return transport.run(
            "transport_unbundle",
            ["bundle", "unbundle", str(source)],
            config=("transfer.fsckObjects=true",),
            timeout=NETWORK_TIMEOUT,
        ).stdout
