"""The errors and the shared interface of a pinned project folder; the POSIX and Windows classes implement it."""

import os
import re
import stat
from abc import ABC, abstractmethod
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Self


class SafeRootError(OSError):
    """A path is missing, unsafe, or cannot be used below the pinned root."""


class RootReplacedError(SafeRootError):
    """The pinned project folder, or a folder below it that was already opened, is no longer the same folder."""


def relative_parts(path: str) -> tuple[str, ...]:
    """Normalize a relative path and refuse absolute paths or traversal above the root."""
    clean = path.replace("\\", "/")
    candidate = PurePosixPath(clean)
    if candidate.is_absolute() or re.match(r"^[A-Za-z]:", clean):
        raise SafeRootError("The path must be relative to the project folder.")
    parts: list[str] = []
    for part in candidate.parts:
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                raise SafeRootError("The path leaves the project folder.")
            parts.pop()
        else:
            parts.append(part)
    return tuple(parts)


class SafeRootBase(ABC):
    """A pinned directory. The checks every platform shares live here; how a path is resolved is per platform."""

    def __init__(self, root: Path) -> None:
        # Set first so close() is safe when opening the root fails.
        self._root_fd = -1
        self.path = self._pinned_path(Path(os.path.abspath(root)))

    @staticmethod
    @abstractmethod
    def _pinned_path(absolute: Path) -> Path:
        """The root path as this platform pins it."""

    def close(self) -> None:
        if self._root_fd >= 0:
            os.close(self._root_fd)
            self._root_fd = -1

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()

    @staticmethod
    def _reject_git_path(parts: tuple[str, ...]) -> None:
        git_name = ".git"
        if any(part.casefold() == git_name for part in parts):
            raise SafeRootError("Writing into .git is not allowed.")

    @abstractmethod
    def _open_descriptor(self, parts: tuple[str, ...], flags: int, mode: int) -> int: ...

    @abstractmethod
    def _write(self, parts: tuple[str, ...], data: bytes) -> None: ...

    @abstractmethod
    def _remove(self, parts: tuple[str, ...]) -> None: ...

    @abstractmethod
    def _remove_tree(self, parts: tuple[str, ...]) -> None: ...

    @abstractmethod
    def _replace_folder(self, parts: tuple[str, ...], files: dict[str, bytes]) -> None: ...

    @abstractmethod
    def is_dir(self, path: str) -> bool: ...

    @abstractmethod
    def stat(self, path: str) -> os.stat_result | None: ...

    @abstractmethod
    def ensure_directory(self, path: str) -> None: ...

    @abstractmethod
    def canonical_parts(self, path: str) -> tuple[str, ...]:
        """The in-root path a write resolves to, including existing symlink components."""

    @abstractmethod
    def entries(
        self, path: str, recursive: bool = True, skip_dirs: set[str] | None = None
    ) -> Iterator[tuple[str, os.stat_result]]:
        """Yield entries without following directory links."""

    def open_file(self, path: str, flags: int = os.O_RDONLY, mode: int = 0o666) -> int:
        parts = relative_parts(path)
        if not parts:
            raise SafeRootError("The project root is not a file.")
        descriptor = self._open_descriptor(parts, flags, mode)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            os.close(descriptor)
            raise SafeRootError("The path is not a regular file.")
        return descriptor

    def read_bytes(self, path: str) -> bytes:
        descriptor = self.open_file(path)
        with os.fdopen(descriptor, "rb") as file:
            return file.read()

    def read_limited(self, path: str, limit: int) -> bytes | None:
        descriptor = self.open_file(path)
        with os.fdopen(descriptor, "rb") as file:
            if os.fstat(file.fileno()).st_size > limit:
                return None
            data = file.read(limit + 1)
        return data if len(data) <= limit else None

    def is_file(self, path: str) -> bool:
        try:
            descriptor = self.open_file(path)
        except OSError:
            return False
        os.close(descriptor)
        return True

    def write_bytes(self, path: str, data: bytes) -> None:
        parts = relative_parts(path)
        if not parts:
            raise SafeRootError("The project root is not a file.")
        self._write(parts, data)

    def remove(self, path: str) -> None:
        parts = relative_parts(path)
        if not parts:
            raise SafeRootError("The project root cannot be removed.")
        self._remove(parts)

    def remove_tree(self, path: str) -> None:
        parts = relative_parts(path)
        if not parts:
            raise SafeRootError("The project root cannot be removed.")
        self._remove_tree(parts)

    def replace_folder_contents(self, path: str, files: dict[str, bytes]) -> None:
        """Atomically replace a directory using descriptors rooted in this project."""
        parts = relative_parts(path)
        if not parts:
            raise SafeRootError("The project root cannot be replaced.")
        self._reject_git_path(parts)
        for file_path in files:
            normalized = relative_parts(file_path)
            if not normalized:
                raise SafeRootError("A replacement file path must not be empty.")
            self._reject_git_path((*parts, *normalized))
        self._replace_folder(parts, files)
