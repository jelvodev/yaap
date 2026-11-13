"""The pinned project folder on Windows, where a directory cannot be opened as a descriptor.

Paths are resolved and checked to stay below the root; there is no descriptor pinning or identity check here.
"""

import os
import secrets
import shutil
from collections.abc import Iterator
from pathlib import Path

from app.targets.safe_root_base import SafeRootBase, SafeRootError, relative_parts


class WindowsSafeRoot(SafeRootBase):
    @staticmethod
    def _pinned_path(absolute: Path) -> Path:
        return absolute.resolve()

    def _open_descriptor(self, parts: tuple[str, ...], flags: int, mode: int) -> int:
        target = (self.path.joinpath(*parts)).resolve()
        if not target.is_relative_to(self.path):
            raise SafeRootError("The path leaves the project folder.")
        return os.open(target, flags, mode)

    def ensure_directory(self, path: str) -> None:
        parts = relative_parts(path)
        target = self.path.joinpath(*parts).resolve()
        if not target.is_relative_to(self.path):
            raise SafeRootError("The path leaves the project folder.")
        target.mkdir(parents=True, exist_ok=True)

    def is_dir(self, path: str) -> bool:
        try:
            target = self.path.joinpath(*relative_parts(path)).resolve()
            return target.is_relative_to(self.path) and target.is_dir()
        except OSError:
            return False

    def stat(self, path: str) -> os.stat_result | None:
        parts = relative_parts(path)
        if not parts:
            return self.path.stat()
        parent = self.path.joinpath(*parts[:-1]).resolve()
        if not parent.is_relative_to(self.path):
            raise SafeRootError("The path leaves the project folder.")
        target = parent / parts[-1]
        try:
            return target.lstat()
        except FileNotFoundError:
            return None

    def _write(self, parts: tuple[str, ...], data: bytes) -> None:
        target = self.path.joinpath(*parts)
        if not target.resolve().is_relative_to(self.path):
            raise SafeRootError("The path leaves the project folder.")
        self._reject_git_path(tuple(target.resolve().relative_to(self.path).parts))
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".yaap-{secrets.token_hex(12)}")
        temporary.write_bytes(data)
        os.replace(temporary, target)

    def _remove(self, parts: tuple[str, ...]) -> None:
        target = self.path.joinpath(*parts)
        if target.resolve().is_relative_to(self.path):
            self._reject_git_path(tuple(target.resolve().relative_to(self.path).parts))
            target.unlink(missing_ok=True)

    def _remove_tree(self, parts: tuple[str, ...]) -> None:
        target = self.path.joinpath(*parts)
        resolved = target.resolve()
        if not resolved.is_relative_to(self.path):
            raise SafeRootError("The path leaves the project folder.")
        if resolved.is_dir():
            shutil.rmtree(resolved)
        else:
            target.unlink(missing_ok=True)

    def _remove_empty_dir(self, parts: tuple[str, ...]) -> bool:
        target = self.path.joinpath(*parts)
        if not target.parent.resolve().is_relative_to(self.path):
            raise SafeRootError("The path leaves the project folder.")
        if target.is_symlink() or target.is_junction():
            return False
        try:
            target.rmdir()
        except OSError:
            return False
        return True

    def _replace_folder(self, parts: tuple[str, ...], files: dict[str, bytes]) -> None:
        target = self.path.joinpath(*parts)
        if not target.resolve().is_relative_to(self.path):
            raise SafeRootError("The path leaves the project folder.")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.joinpath(*parts[:-1], f".yaap-{secrets.token_hex(12)}")
        temporary.mkdir()
        try:
            for name, content in files.items():
                relative = relative_parts(name)
                file = temporary.joinpath(*relative)
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(content)
            if target.exists():
                shutil.rmtree(target)
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def canonical_parts(self, path: str) -> tuple[str, ...]:
        """The in-root path a write resolves to, including existing symlink components."""
        parts = relative_parts(path)
        if not parts:
            return ()
        target = self.path.joinpath(*parts).resolve()
        if not target.is_relative_to(self.path):
            raise SafeRootError("The path leaves the project folder.")
        return tuple(target.relative_to(self.path).parts)

    def entries(
        self, path: str, recursive: bool = True, skip_dirs: set[str] | None = None
    ) -> Iterator[tuple[str, os.stat_result]]:
        """Yield entries without following directory links."""
        parts = relative_parts(path)
        skipped = skip_dirs or set()
        folder = self.path.joinpath(*parts).resolve()
        if not folder.is_relative_to(self.path):
            raise SafeRootError("The path leaves the project folder.")
        for current, folders, files in os.walk(folder, followlinks=False):
            folders[:] = sorted(name for name in folders if name not in skipped)
            names = [*folders, *sorted(files)]
            for name in names:
                entry = Path(current) / name
                yield entry.relative_to(self.path).as_posix(), entry.lstat()
            if not recursive:
                break
