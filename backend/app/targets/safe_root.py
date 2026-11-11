"""Descriptor-relative access to files below one pinned directory."""

import os
import re
import secrets
import shutil
import stat
import uuid
from collections.abc import Iterator
from pathlib import Path, PurePosixPath

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


class SafeRootError(OSError):
    """A path is missing, unsafe, or cannot be used below the pinned root."""


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


class SafeRoot:
    """A pinned directory; POSIX operations stay relative to its open descriptor."""

    def __init__(self, root: Path) -> None:
        self._root_fd = -1
        absolute = Path(os.path.abspath(root))
        self.path = absolute if os.name == "posix" else absolute.resolve()
        self._root_fd = self._open_root(self.path) if os.name == "posix" else -1

    @staticmethod
    def _open_root(path: Path) -> int:
        if os.name != "posix":
            return os.open(path.resolve(), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
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

    def close(self) -> None:
        if self._root_fd >= 0:
            os.close(self._root_fd)
            self._root_fd = -1

    def __enter__(self) -> SafeRoot:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()

    def _check_root_identity(self) -> None:
        if os.name != "posix":
            return
        try:
            current = self._open_root(self.path)
        except OSError as error:
            raise SafeRootError("The project folder was replaced.") from error
        try:
            pinned = os.fstat(self._root_fd)
            found = os.fstat(current)
            if (pinned.st_dev, pinned.st_ino) != (found.st_dev, found.st_ino):
                raise SafeRootError("The project folder was replaced.")
        finally:
            os.close(current)

    @staticmethod
    def _reject_git_path(parts: tuple[str, ...]) -> None:
        git_name = ".git"
        if any(part.casefold() == git_name for part in parts):
            raise SafeRootError("Writing into .git is not allowed.")

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
                        target = os.readlink(part, dir_fd=current)
                        suffix = pending[index + 1 :]
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
                        pending = normalized
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
        """A descriptor for a directory below the root; POSIX only, since Windows cannot open a directory this way."""
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
        parts = relative_parts(path)
        if os.name != "posix":
            target = self.path.joinpath(*parts).resolve()
            if not target.is_relative_to(self.path):
                raise SafeRootError("The path leaves the project folder.")
            target.mkdir(parents=True, exist_ok=True)
            return
        os.close(self._directory(parts, create=True))

    def _verify_parent_link(self, descriptor: int, parts: tuple[str, ...]) -> None:
        """Reject a parent directory that was moved after its descriptor was opened."""
        if os.name != "posix":
            return
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
                raise SafeRootError("The parent folder was replaced.")
        except OSError as error:
            if isinstance(error, SafeRootError):
                raise
            raise SafeRootError("The parent folder was replaced.") from error
        finally:
            os.close(current)

    def open_file(self, path: str, flags: int = os.O_RDONLY, mode: int = 0o666) -> int:
        parts = relative_parts(path)
        if not parts:
            raise SafeRootError("The project root is not a file.")
        if os.name != "posix":
            target = (self.path.joinpath(*parts)).resolve()
            if not target.is_relative_to(self.path):
                raise SafeRootError("The path leaves the project folder.")
            descriptor = os.open(target, flags, mode)
        else:
            parent_fd, leaf, canonical = self._resolve(parts)
            try:
                self._verify_parent_link(parent_fd, canonical[:-1])
                descriptor = os.open(leaf, flags | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0), mode, dir_fd=parent_fd)
            finally:
                os.close(parent_fd)
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

    def is_dir(self, path: str) -> bool:
        if os.name != "posix":
            try:
                target = self.path.joinpath(*relative_parts(path)).resolve()
                return target.is_relative_to(self.path) and target.is_dir()
            except OSError:
                return False
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
            return os.fstat(self._root_fd) if os.name == "posix" else self.path.stat()
        if os.name != "posix":
            parent = self.path.joinpath(*parts[:-1]).resolve()
            if not parent.is_relative_to(self.path):
                raise SafeRootError("The path leaves the project folder.")
            target = parent / parts[-1]
            try:
                return target.lstat()
            except FileNotFoundError:
                return None
        parent_fd, leaf, _canonical = self._resolve(parts, follow_final=False, allow_missing=True)
        try:
            return os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        finally:
            os.close(parent_fd)

    def write_bytes(self, path: str, data: bytes) -> None:
        parts = relative_parts(path)
        if not parts:
            raise SafeRootError("The project root is not a file.")
        if os.name != "posix":
            target = self.path.joinpath(*parts)
            if not target.resolve().is_relative_to(self.path):
                raise SafeRootError("The path leaves the project folder.")
            self._reject_git_path(tuple(target.resolve().relative_to(self.path).parts))
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".yaap-{secrets.token_hex(12)}")
            temporary.write_bytes(data)
            os.replace(temporary, target)
            return
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
            for _ in range(10):
                temporary = f".yaap-{secrets.token_hex(12)}"
                try:
                    descriptor = os.open(
                        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o666, dir_fd=parent_fd
                    )
                    break
                except FileExistsError:
                    temporary = None
            if temporary is None:
                raise SafeRootError("Could not create a temporary file safely.")
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

    def remove(self, path: str) -> None:
        parts = relative_parts(path)
        if not parts:
            raise SafeRootError("The project root cannot be removed.")
        if os.name != "posix":
            target = self.path.joinpath(*parts)
            if target.resolve().is_relative_to(self.path):
                self._reject_git_path(tuple(target.resolve().relative_to(self.path).parts))
                target.unlink(missing_ok=True)
            return
        parent_fd, leaf, canonical = self._resolve(parts, follow_final=False)
        try:
            self._verify_parent_link(parent_fd, canonical[:-1])
            self._reject_git_path(canonical)
            os.unlink(leaf, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        finally:
            os.close(parent_fd)

    def remove_tree(self, path: str) -> None:
        parts = relative_parts(path)
        if not parts:
            raise SafeRootError("The project root cannot be removed.")
        if os.name != "posix":
            target = self.path.joinpath(*parts)
            resolved = target.resolve()
            if not resolved.is_relative_to(self.path):
                raise SafeRootError("The path leaves the project folder.")
            if resolved.is_dir():
                shutil.rmtree(resolved)
            else:
                target.unlink(missing_ok=True)
            return
        parent_fd, leaf, canonical = self._resolve(parts, follow_final=False)
        try:
            self._verify_parent_link(parent_fd, canonical[:-1])
            self._reject_git_path(canonical)
            _remove_entry_at(parent_fd, leaf)
        finally:
            os.close(parent_fd)

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
        if os.name != "posix":
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
            return
        self._replace_folder_posix(parts, files)

    def _replace_folder_posix(self, parts: tuple[str, ...], files: dict[str, bytes]) -> None:
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
        if os.name != "posix":
            target = self.path.joinpath(*parts).resolve()
            if not target.is_relative_to(self.path):
                raise SafeRootError("The path leaves the project folder.")
            return tuple(target.relative_to(self.path).parts)
        parent_fd, _leaf, canonical = self._resolve(parts, allow_missing=True)
        os.close(parent_fd)
        return canonical

    def entries(
        self, path: str, recursive: bool = True, skip_dirs: set[str] | None = None
    ) -> Iterator[tuple[str, os.stat_result]]:
        """Yield entries without following directory links."""
        parts = relative_parts(path)
        skipped = skip_dirs or set()
        if os.name != "posix":
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
            return
        root_fd = self._directory(parts)

        def visit(descriptor: int, prefix: tuple[str, ...]) -> Iterator[tuple[str, os.stat_result]]:
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
                        yield from visit(child, (*prefix, name))
                    finally:
                        os.close(child)

        try:
            yield from visit(root_fd, parts)
        finally:
            os.close(root_fd)
