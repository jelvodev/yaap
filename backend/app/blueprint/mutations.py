"""Filesystem mutations and path-scoped commits for Blueprint layers."""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from app.git.place import GitPlace, git_place, git_place_kind
from app.git.service import GitError, init_repository, run_git
from app.runner.service import RUNNERS
from app.targets.safe_root import SafeRoot

if TYPE_CHECKING:
    from app.blueprint.layers import Layer


@dataclass(frozen=True)
class MutationResult:
    """Whether a saved Blueprint change also reached git history."""

    committed: bool
    error: str | None = None


def layer_place(layer: Layer) -> GitPlace:
    """Where git runs for the layer's repository. A project layer in the workspace goes to its environment's runner;
    the global and environment layers are outside the workspace and stay in YAAP. Synchronous: call it from a worker
    thread. Raises GitError when the runner is needed and cannot be used, also for a layer without an environment."""
    if layer.environment_id is None:
        if git_place_kind(layer.repository, RUNNERS.workspace_dir, RUNNERS.enabled) == "runner":
            raise GitError("This Blueprint layer is in the workspace but does not name its environment.")
        return GitPlace(layer.repository)
    return git_place(layer.environment_id, layer.repository)


def ensure_repository(layer: Layer) -> None:
    if layer.kind == "project":
        with SafeRoot(layer.repository) as root:
            root.ensure_directory(layer.prefix.rstrip("/"))
        return
    layer.root.mkdir(parents=True, exist_ok=True)
    init_repository(layer.repository)


def write(layer: Layer, path: str, content: str, message: str) -> MutationResult:
    write_bytes(layer, path, content.replace("\r\n", "\n").encode("utf-8"))
    return commit_paths(layer, [path], message)


def write_bytes(layer: Layer, path: str, content: bytes) -> None:
    if layer.kind == "project":
        with SafeRoot(layer.repository) as root:
            root.write_bytes(f"{layer.prefix}{path}", content)
        return
    target = layer.root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


def delete(layer: Layer, path: str, message: str) -> MutationResult:
    if layer.kind == "project":
        relative = f"{layer.prefix}{path}"
        with SafeRoot(layer.repository) as root:
            info = root.stat(relative)
            if info is not None and stat.S_ISDIR(info.st_mode):
                root.remove_tree(relative)
            else:
                root.remove(relative)
        return commit_paths(layer, [path], message)
    target = layer.root / path
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    if target.parent != layer.root and target.parent.is_dir() and not any(target.parent.iterdir()):
        target.parent.rmdir()
    return commit_paths(layer, [path], message)


def replace_folder(layer: Layer, folder: str, files: dict[str, bytes], message: str) -> MutationResult:
    """Build a folder beside its target, then swap it in; restore the old folder if the swap fails."""
    replace_folder_contents(layer, folder, files)
    return commit_paths(layer, [folder], message)


def replace_folder_contents(layer: Layer, folder: str, files: dict[str, bytes]) -> None:
    """Atomically replace a folder without committing, for a multi-path install transaction."""
    relative = PurePosixPath(folder)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts or "." in relative.parts:
        raise ValueError("A replacement folder must stay inside its Blueprint layer.")
    if layer.kind == "project":
        with SafeRoot(layer.repository) as root:
            root.replace_folder_contents(f"{layer.prefix}{relative.as_posix()}", files)
        return
    target = layer.root.joinpath(*relative.parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.new-", dir=target.parent))
    backup = target.parent / f".{target.name}.backup-{uuid.uuid4().hex}"
    try:
        for name, content in files.items():
            file_path = PurePosixPath(name)
            if file_path.is_absolute() or not file_path.parts or ".." in file_path.parts or "." in file_path.parts:
                raise ValueError("A replacement file must stay inside its folder.")
            file = temporary.joinpath(*file_path.parts)
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(content)
        moved_old = False
        try:
            if target.exists():
                os.replace(target, backup)
                moved_old = True
            os.replace(temporary, target)
        except OSError:
            if moved_old and backup.exists():
                os.replace(backup, target)
            raise
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    if backup.exists():
        shutil.rmtree(backup)


def commit_paths(layer: Layer, paths: list[str], message: str) -> MutationResult:
    """Commit only the requested paths, leaving unrelated staged work alone."""
    pathspecs = [f"{layer.prefix}{path}" for path in paths]
    try:
        place = layer_place(layer)
        run_git(["add", "--all", "--", *pathspecs], place)
        if run_git(["status", "--porcelain", "--", *pathspecs], place).stdout.strip():
            run_git(["commit", "-m", message, "--", *pathspecs], place)
    except GitError as error:
        return MutationResult(False, str(error))
    return MutationResult(True)
