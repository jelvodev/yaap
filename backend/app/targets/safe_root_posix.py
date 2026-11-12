"""The pinned project folder on POSIX: every operation stays relative to the root's open descriptor."""

import os
import secrets
import stat
import uuid
from collections.abc import Iterator
from pathlib import Path

from app.targets.safe_root_base import RootReplacedError, SafeRootBase, SafeRootError, relative_parts

MAX_SYMLINKS = 40


def _write_tree(root_fd: int, files: dict[str, bytes]) -> None:
    for path, content in files.items():
        parts = relative_parts(path)
        current = os.dup(root_fd)
        try:
            for part in parts[:-1]:
                try:
                    os.mkdir(part, dir_fd=current)
                except FileExistsError:
                    pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
                os.close(current)
                current = child
            descriptor = os.open(
                parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o666, dir_fd=current
            )
            with os.fdopen(descriptor, "wb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
        finally:
            os.close(current)


def _remove_entry_at(parent_fd: int, name: str) -> None:
    try:
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if stat.S_ISDIR(info.st_mode):
        descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        try:
            for child in os.listdir(descriptor):
                _remove_entry_at(descriptor, child)
        finally:
            os.close(descriptor)
        os.rmdir(name, dir_fd=parent_fd)
    else:
        os.unlink(name, dir_fd=parent_fd)


class PosixSafeRoot(SafeRootBase):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self._root_fd = self._open_root(self.path)

    @staticmethod
    def _pinned_path(absolute: Path) -> Path:
        return absolute

    @staticmethod
    def _open_root(path: Path) -> int:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptor = os.open(path.anchor, flags)
        try:
            for part in path.parts[1:]:
                next_fd = os.open(part, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = next_fd
            return descriptor
        except OSError:
            os.close(descriptor)
            raise

    def _check_root_identity(self) -> None:
        try:
            current = self._open_root(self.path)
        except OSError as error:
            raise RootReplacedError("The project folder was replaced.") from error
        try:
            pinned = os.fstat(self._root_fd)
            found = os.fstat(current)
            if (pinned.st_dev, pinned.st_ino) != (found.st_dev, found.st_ino):
                raise RootReplacedError("The project folder was replaced.")
        finally:
            os.close(current)

    def _followed_link(self, current: int, part: str, prefix: list[str], suffix: list[str]) -> list[str]:
        """The pending path after the symbolic link `part` is followed, still below this root."""
        target = os.readlink(part, dir_fd=current)
        if os.path.isabs(target):
            try:
                target_parts = list(Path(target).relative_to(self.path).parts)
            except ValueError as error:
                raise SafeRootError("The symbolic link leaves the project folder.") from error
        else:
            target_parts = prefix + target.replace("\\", "/").split("/")
        normalized: list[str] = []
        for component in [*target_parts, *suffix]:
            if component in ("", "."):
                continue
            if component == "..":
                if not normalized:
                    raise SafeRootError("The symbolic link leaves the project folder.")
                normalized.pop()
            else:
                normalized.append(component)
        return normalized

    def _resolve(
        self,
        parts: tuple[str, ...],
        *,
        create_parents: bool = False,
        follow_final: bool = True,
        allow_missing: bool = False,
    ) -> tuple[int, str, tuple[str, ...]]:
        """Return a parent descriptor and leaf, resolving only links that stay below this root."""
        self._check_root_identity()
        pending = list(parts)
        followed = 0
        while pending:
            current = os.dup(self._root_fd)
            prefix: list[str] = []
            restart = False
            try:
                for index, part in enumerate(pending):
                    final = index == len(pending) - 1
                    try:
                        info = os.stat(part, dir_fd=current, follow_symlinks=False)
                    except FileNotFoundError:
                        if final or allow_missing:
                            return current, part, tuple([*prefix, *pending[index:]])
                        if not create_parents:
                            raise
                        os.mkdir(part, dir_fd=current)
                        info = os.stat(part, dir_fd=current, follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode) and (follow_final or not final):
                        followed += 1
                        if followed > MAX_SYMLINKS:
                            raise SafeRootError("Too many symbolic links in the path.")
                        pending = self._followed_link(current, part, prefix, pending[index + 1 :])
                        restart = True
                        break
                    if final:
                        return current, part, tuple([*prefix, part])
                    if not stat.S_ISDIR(info.st_mode):
                        raise NotADirectoryError(part)
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
                    os.close(current)
                    current = child
                    prefix.append(part)
                if not restart:
                    raise SafeRootError("The path does not name a file.")
            except OSError:
                os.close(current)
                raise
            os.close(current)
        raise SafeRootError("A symbolic link cannot replace the project root.")

    def _directory(self, parts: tuple[str, ...], *, create: bool = False) -> int:
        """A descriptor for a directory below the root."""
        self._check_root_identity()
        if not parts:
            return os.dup(self._root_fd)
        parent_fd, leaf, _canonical = self._resolve(parts, create_parents=create)
        try:
            try:
                return os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(leaf, dir_fd=parent_fd)
                return os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        finally:
            os.close(parent_fd)

    def ensure_directory(self, path: str) -> None:
        os.close(self._directory(relative_parts(path), create=True))

    def _verify_parent_link(self, descriptor: int, parts: tuple[str, ...]) -> None:
        """Reject a parent directory that was moved after its descriptor was opened."""
        self._check_root_identity()
        current = os.dup(self._root_fd)
        try:
            for part in parts:
                following = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current
                )
                os.close(current)
                current = following
            pinned = os.fstat(descriptor)
            found = os.fstat(current)
            if (pinned.st_dev, pinned.st_ino) != (found.st_dev, found.st_ino):
                raise RootReplacedError("The parent folder was replaced.")
        except OSError as error:
            if isinstance(error, SafeRootError):
                raise
            raise RootReplacedError("The parent folder was replaced.") from error
        finally:
            os.close(current)

    def _open_descriptor(self, parts: tuple[str, ...], flags: int, mode: int) -> int:
        parent_fd, leaf, canonical = self._resolve(parts)
        try:
            self._verify_parent_link(parent_fd, canonical[:-1])
            return os.open(leaf, flags | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0), mode, dir_fd=parent_fd)
        finally:
            os.close(parent_fd)

    def is_dir(self, path: str) -> bool:
        try:
            descriptor = self._directory(relative_parts(path))
        except OSError:
            return False
        os.close(descriptor)
        return True

    def stat(self, path: str) -> os.stat_result | None:
        self._check_root_identity()
        parts = relative_parts(path)
        if not parts:
            return os.fstat(self._root_fd)
        parent_fd, leaf, _canonical = self._resolve(parts, follow_final=False, allow_missing=True)
        try:
            return os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        finally:
            os.close(parent_fd)

    def _create_temporary(self, parent_fd: int) -> tuple[str, int]:
        """A new temporary file next to the destination: its name and an open descriptor."""
        for _ in range(10):
            temporary = f".yaap-{secrets.token_hex(12)}"
            try:
                descriptor = os.open(
                    temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o666, dir_fd=parent_fd
                )
                return temporary, descriptor
            except FileExistsError:
                continue
        raise SafeRootError("Could not create a temporary file safely.")

    def _write(self, parts: tuple[str, ...], data: bytes) -> None:
        parent_fd, leaf, canonical = self._resolve(parts, create_parents=True)
        temporary: str | None = None
        try:
            self._verify_parent_link(parent_fd, canonical[:-1])
            self._reject_git_path(canonical)
            try:
                existing = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
                if not stat.S_ISREG(existing.st_mode):
                    raise SafeRootError("The destination is not a regular file.")
                preserve_mode = stat.S_IMODE(existing.st_mode)
            except FileNotFoundError:
                preserve_mode = None
            temporary, descriptor = self._create_temporary(parent_fd)
            if preserve_mode is not None:
                os.fchmod(descriptor, preserve_mode)
            with os.fdopen(descriptor, "wb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            self._verify_parent_link(parent_fd, canonical[:-1])
            os.replace(temporary, leaf, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
            os.close(parent_fd)

    def _remove(self, parts: tuple[str, ...]) -> None:
        parent_fd, leaf, canonical = self._resolve(parts, follow_final=False)
        try:
            self._verify_parent_link(parent_fd, canonical[:-1])
            self._reject_git_path(canonical)
            os.unlink(leaf, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        finally:
            os.close(parent_fd)

    def _remove_tree(self, parts: tuple[str, ...]) -> None:
        parent_fd, leaf, canonical = self._resolve(parts, follow_final=False)
        try:
            self._verify_parent_link(parent_fd, canonical[:-1])
            self._reject_git_path(canonical)
            _remove_entry_at(parent_fd, leaf)
        finally:
            os.close(parent_fd)

    def _replace_folder(self, parts: tuple[str, ...], files: dict[str, bytes]) -> None:
        parent_parts = parts[:-1]
        parent_fd = self._directory(parent_parts, create=True)
        canonical_parent = self.canonical_parts("/".join(parent_parts)) if parent_parts else ()
        temporary = f".yaap-{secrets.token_hex(12)}"
        backup = f".yaap-{uuid.uuid4().hex}.backup"
        moved_old = False
        try:
            self._verify_parent_link(parent_fd, canonical_parent)
            os.mkdir(temporary, dir_fd=parent_fd)
            temp_fd = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
            try:
                _write_tree(temp_fd, files)
            finally:
                os.close(temp_fd)
            try:
                info = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                info = None
            if info is not None:
                if not stat.S_ISDIR(info.st_mode):
                    raise SafeRootError("The folder to replace is not a directory.")
                os.rename(parts[-1], backup, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                moved_old = True
            try:
                self._verify_parent_link(parent_fd, canonical_parent)
                os.rename(temporary, parts[-1], src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            except OSError:
                if moved_old:
                    os.rename(backup, parts[-1], src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                    moved_old = False
                raise
            if moved_old:
                _remove_entry_at(parent_fd, backup)
                moved_old = False
        finally:
            try:
                _remove_entry_at(parent_fd, temporary)
            except FileNotFoundError:
                pass
            os.close(parent_fd)

    def canonical_parts(self, path: str) -> tuple[str, ...]:
        """The in-root path a write resolves to, including existing symlink components."""
        self._check_root_identity()
        parts = relative_parts(path)
        if not parts:
            return ()
        parent_fd, _leaf, canonical = self._resolve(parts, allow_missing=True)
        os.close(parent_fd)
        return canonical

    def _visit(
        self, descriptor: int, prefix: tuple[str, ...], recursive: bool, skipped: set[str]
    ) -> Iterator[tuple[str, os.stat_result]]:
        for name in sorted(os.listdir(descriptor)):
            try:
                info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            except OSError:
                continue
            if name in skipped:
                continue
            relative = "/".join((*prefix, name))
            yield relative, info
            if recursive and stat.S_ISDIR(info.st_mode):
                try:
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                except OSError:
                    continue
                try:
                    yield from self._visit(child, (*prefix, name), recursive, skipped)
                finally:
                    os.close(child)

    def entries(
        self, path: str, recursive: bool = True, skip_dirs: set[str] | None = None
    ) -> Iterator[tuple[str, os.stat_result]]:
        """Yield entries without following directory links."""
        parts = relative_parts(path)
        skipped = skip_dirs or set()
        root_fd = self._directory(parts)
        try:
            yield from self._visit(root_fd, parts, recursive, skipped)
        finally:
            os.close(root_fd)
