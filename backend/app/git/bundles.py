"""Moving commits between the transport and the work repository as git bundles.

The work repository's git directory is never given to a process that holds the token. A bundle is only a pack of
objects plus the names of its refs: this module applies those names to `refs/remotes/origin/*` itself, after checking
them, and a bundle a place makes for a push must hold exactly the branch and commit that was asked for.
"""

import re
import tempfile
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from app.git.common import GitError
from app.git.executor import capture_git, decode
from app.git.place import GitPlace, run_from_worker
from app.git.service import Repository, executor_of
from app.git.safety import ConfigEntry
from app.git.transport import (
    NETWORK_TIMEOUT,
    Transport,
    branch_tip,
    bundle_all_branches,
    network_step,
    remote_push,
    unbundle_into_transport,
    write_delivered,
)
from app.runner.service import runner_path
from app.targets.base import TargetError
from app.targets.safe_root import SafeRoot, SafeRootError

BRANCH_PREFIX = "refs/heads/"
TRACKING_PREFIX = "refs/remotes/origin/"
INCOMING_FOLDER = ".yaap-incoming"
OBJECT_ID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
FORBIDDEN_CHARACTERS = re.compile(r"[\x00-\x20\x7f~^:?*\[\\]")


@dataclass(frozen=True)
class RefUpdate:
    """Set a ref to a commit, or delete it when `sha` is None."""

    ref: str
    sha: str | None


def safe_branch_name(name: str) -> bool:
    """A branch name YAAP is willing to put in a ref name and on a command line: git's rules, a little stricter."""
    if not name or name in ("HEAD", "@") or FORBIDDEN_CHARACTERS.search(name):
        return False
    if name.startswith("-") or name.endswith(".") or ".." in name or "@{" in name:
        return False
    return all(part and not part.startswith(".") and not part.endswith(".lock") for part in name.split("/"))


def branch_of(ref: str, prefix: str = BRANCH_PREFIX) -> str | None:
    """The branch name of `<prefix><name>`, or None when the ref is something else or its name is not safe."""
    name = ref.removeprefix(prefix)
    return name if ref.startswith(prefix) and safe_branch_name(name) else None


def tracking_updates(
    bundle_refs: Sequence[tuple[str, str]], current_tracking: Mapping[str, str]
) -> tuple[RefUpdate, ...]:
    """What to change in `refs/remotes/origin/*` so it mirrors the branches of a bundle: set each, delete those the
    bundle no longer has. `origin/HEAD` and refs with unsafe names are left alone."""
    wanted = {
        TRACKING_PREFIX + name: sha
        for sha, ref in bundle_refs
        if (name := branch_of(ref)) is not None and OBJECT_ID.fullmatch(sha)
    }
    changes = [RefUpdate(ref, sha) for ref, sha in sorted(wanted.items()) if current_tracking.get(ref) != sha]
    stale = [ref for ref in sorted(current_tracking) if ref not in wanted and branch_of(ref, TRACKING_PREFIX)]
    return (*changes, *(RefUpdate(ref, None) for ref in stale))


def parse_ref_lines(output: str) -> tuple[tuple[str, str], ...]:
    """(commit, ref) from lines of `<commit> <ref>`, as `git bundle unbundle` and `git for-each-ref` print them."""
    pairs = (line.strip().split(" ", 1) for line in output.splitlines() if " " in line.strip())
    return tuple((sha, ref) for sha, ref in pairs)


def check_push_bundle(refs: Sequence[tuple[str, str]], branch: str, sha: str) -> None:
    """A bundle made for a push holds exactly the branch asked for, at the commit asked for."""
    if list(refs) != [(sha, BRANCH_PREFIX + branch)]:
        raise GitError("The branch changed while it was being sent. Nothing was pushed; try again.")


@contextmanager
def bundle_on_place(place: Repository, data: bytes) -> Iterator[str]:
    """The path, as the place sees it, of a file holding `data`. In YAAP it is a temporary file; in a runner it is a
    file YAAP writes below the environment's folder, link-safely and outside any repository's `.git`."""
    if executor_of(place) is None:
        with tempfile.TemporaryDirectory(prefix="yaap-bundle-") as folder:
            target = Path(folder) / "incoming.bundle"
            target.write_bytes(data)
            yield str(target)
        return
    environment_dir = place.environment_dir if isinstance(place, GitPlace) else None
    if environment_dir is None:
        raise GitError("This repository's runner folder is unknown, so commits cannot be handed to it.")
    relative = f"{INCOMING_FOLDER}/{uuid.uuid4().hex}.bundle"
    try:
        with SafeRoot(environment_dir) as root:
            root.ensure_directory(INCOMING_FOLDER)
            root.write_bytes(relative, data)
            try:
                yield runner_path(environment_dir, str(environment_dir / relative))
            finally:
                try:
                    root.remove(relative)
                except SafeRootError:
                    pass  # best effort: the file only holds objects the repository has by now
    except (SafeRootError, TargetError) as error:
        raise GitError(f"Commits could not be handed to the runner: {error}") from error


def deliver_branches(transport: Transport) -> None:
    """Bring every branch of the transport to the work repository as `origin/*` remote-tracking branches."""
    data = bundle_all_branches(transport)
    listed: tuple[tuple[str, str], ...] = ()
    if data is not None:
        with bundle_on_place(transport.place, data) as path:
            listed = parse_ref_lines(
                transport.run("work_unbundle", ["bundle", "unbundle", path], timeout=NETWORK_TIMEOUT).stdout
            )
    tracking = transport.run("work_read", ["for-each-ref", "--format=%(objectname) %(refname)", TRACKING_PREFIX]).stdout
    current = {ref: sha for sha, ref in parse_ref_lines(tracking)}
    for update in tracking_updates(listed, current):
        arguments = ["update-ref", "-d", update.ref] if update.sha is None else ["update-ref", update.ref, update.sha]
        transport.run("work_update_tracking", arguments)
    write_delivered(transport)


def bundle_on_work(transport: Transport, branch: str, exclude: str | None) -> bytes:
    """The work repository's branch as a bundle (without the history `exclude` already has), made by its place."""
    if network_step("work_bundle") != "work":
        raise GitError("A bundle of the work repository is made where the work repository is.")
    refs = [BRANCH_PREFIX + branch, *([f"^{exclude}"] if exclude else [])]
    executor = executor_of(transport.place)
    if executor is not None:
        result = run_from_worker(capture_git, executor, ["bundle", "create", "-", *refs])
        if result.exit_code != 0:
            raise GitError(f"git bundle failed: {decode(result.stderr).strip()}")
        return result.stdout
    with tempfile.TemporaryDirectory(prefix="yaap-bundle-") as folder:
        target = Path(folder) / "outgoing.bundle"
        transport.run("work_bundle", ["bundle", "create", str(target), *refs])
        return target.read_bytes()


def work_has(transport: Transport, arguments: Sequence[str]) -> bool:
    """Whether a yes/no question to the work repository is answered yes (exit 128: it does not know the commit)."""
    return transport.run("work_read", arguments, accept_exit_codes=(0, 1, 128)).exit_code == 0


def push_branch(
    transport: Transport,
    url: str,
    authorization: Sequence[ConfigEntry],
    branch: str,
    remote_branch: str,
    sha: str,
) -> None:
    """Push the work branch's commit from the transport. The objects arrive as a bundle the place makes, checked to hold
    exactly that branch at that commit; afterwards the transport and the work repository both record the new tip."""
    tip = branch_tip(transport, remote_branch)
    if tip != sha:
        has_tip = tip is not None and work_has(transport, ["cat-file", "-e", tip])
        contained = tip is not None and has_tip and work_has(transport, ["merge-base", "--is-ancestor", sha, tip])
        if not contained:
            data = bundle_on_work(transport, branch, tip if has_tip else None)
            check_push_bundle(parse_ref_lines(unbundle_into_transport(transport, data)), branch, sha)
    remote_push(transport, url, authorization, sha, remote_branch)
    transport.run("transport_update_ref", ["update-ref", f"{BRANCH_PREFIX}{remote_branch}", sha])
    transport.run("work_update_tracking", ["update-ref", f"{TRACKING_PREFIX}{remote_branch}", sha])
