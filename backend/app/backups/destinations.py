"""Where backups are copied besides data/backups.

A destination is a folder inside the container (kind folder), which the user mounts from the computer through
docker-compose.override.yml, or an SMB network share (kind smb, app/backups/smb.py). After each backup YAAP copies the
zip to every destination that is on: to a `.partial` file, renamed when complete, then checked by size and SHA-256.
Pruning per destination removes only files that match the backup name pattern, so nothing else there is touched.
"""

import hashlib
import os
import shutil
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Protocol

from app.core.backup import BACKUP_NAME

KINDS = ("folder", "smb")
MAX_DESTINATIONS = 10
CHUNK = 1024 * 1024
DEFAULT_BACKUP_ROOT = "/backup"


class DestinationError(Exception):
    """A destination cannot be used; the message is safe to show."""


@dataclass(frozen=True)
class DestinationConfig:
    name: str
    kind: str
    enabled: bool = True
    keep: int = 7
    # folder: a path inside the container, under the backup root.
    path: str = ""
    # smb: \\server\share\folder as user (domain), the password a vault entry of kind smb_password.
    server: str = ""
    share: str = ""
    folder: str = ""
    user: str = ""
    domain: str = ""
    password_entry: str = ""
    require_encryption: bool = True
    extra: dict[str, Any] = field(default_factory=dict, compare=False)


@dataclass
class RemoteFile:
    name: str
    size: int


class Destination(Protocol):
    config: DestinationConfig

    def describe(self) -> str: ...
    def list_backups(self) -> list[RemoteFile]: ...
    def free_space(self) -> int | None: ...
    def open_write(self, name: str) -> BinaryIO: ...
    def open_read(self, name: str) -> BinaryIO: ...
    def rename(self, old: str, new: str) -> None: ...
    def remove(self, name: str) -> None: ...
    def size_of(self, name: str) -> int | None: ...


def backup_root() -> Path:
    return Path(os.environ.get("YAAP_BACKUP_ROOT", DEFAULT_BACKUP_ROOT))


def data_root() -> Path:
    return Path(os.environ.get("YAAP_DATA_DIR", "data")).resolve()


def text(item: dict[str, Any], key: str, limit: int = 200) -> str:
    value = item.get(key, "")
    if not isinstance(value, str) or len(value) > limit or "\n" in value:
        raise DestinationError(f"{key} must be a line of text of at most {limit} characters.")
    return value.strip()


def parse_destination(item: Any) -> DestinationConfig:
    if not isinstance(item, dict):
        raise DestinationError("Each destination is an object.")
    name = text(item, "name", 40)
    if not name:
        raise DestinationError("Each destination needs a name.")
    kind = item.get("kind")
    if kind not in KINDS:
        raise DestinationError(f"{name}: the kind is folder or smb.")
    keep = item.get("keep", 7)
    if not isinstance(keep, int) or isinstance(keep, bool) or not 1 <= keep <= 365:
        raise DestinationError(f"{name}: keep between 1 and 365 backups.")
    enabled = item.get("enabled", True)
    if not isinstance(enabled, bool):
        raise DestinationError(f"{name}: enabled is true or false.")
    if kind == "folder":
        path = text(item, "path", 300)
        check_folder_path(name, path)
        return DestinationConfig(name=name, kind=kind, enabled=enabled, keep=keep, path=path)
    server, share = text(item, "server", 255), text(item, "share", 80)
    if not server or not share:
        raise DestinationError(f"{name}: a network share needs a server and a share name.")
    if any(character in server for character in "\\/ ") or any(character in share for character in "\\/"):
        raise DestinationError(f"{name}: give the server and the share name without slashes, e.g. nas and backups.")
    folder = text(item, "folder", 300).replace("\\", "/").strip("/")
    if ".." in folder.split("/"):
        raise DestinationError(f"{name}: the folder cannot go up with '..'.")
    encryption = item.get("require_encryption", True)
    if not isinstance(encryption, bool):
        raise DestinationError(f"{name}: require_encryption is true or false.")
    return DestinationConfig(
        name=name,
        kind=kind,
        enabled=enabled,
        keep=keep,
        server=server,
        share=share,
        folder=folder,
        user=text(item, "user", 120),
        domain=text(item, "domain", 120),
        password_entry=text(item, "password_entry", 64),
        require_encryption=encryption,
    )


def check_folder_path(name: str, path: str) -> None:
    """A folder destination lies under the backup root (where the override file mounts it), never in the data."""
    root = backup_root()
    folder = Path(path)
    if not path or not folder.is_absolute() or ".." in folder.parts:
        raise DestinationError(f"{name}: use an absolute folder inside the container, such as {root.as_posix()}.")
    resolved, resolved_root = folder.resolve(), root.resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise DestinationError(
            f"{name}: the folder must be {root.as_posix()} or inside it; mount a folder of your computer there in "
            "docker-compose.override.yml."
        )
    data = data_root()
    if resolved == data or data in resolved.parents:
        raise DestinationError(f"{name}: a destination inside YAAP's data folder does not survive losing that disk.")


def parse_destinations(value: Any) -> list[DestinationConfig]:
    if not isinstance(value, list) or len(value) > MAX_DESTINATIONS:
        raise DestinationError(f"Use a list of at most {MAX_DESTINATIONS} destinations.")
    configs = [parse_destination(item) for item in value]
    names = [config.name.casefold() for config in configs]
    if len(names) != len(set(names)):
        raise DestinationError("Each destination needs its own name.")
    return configs


def validate_destinations(value: Any, _providers: Any) -> str | None:
    try:
        parse_destinations(value)
    except DestinationError as error:
        return str(error)
    return None


class FolderDestination:
    def __init__(self, config: DestinationConfig) -> None:
        self.config = config
        self.folder = Path(config.path)

    def describe(self) -> str:
        return self.config.path

    def ready(self) -> None:
        if not self.folder.is_dir():
            raise DestinationError(
                f"{self.config.path} does not exist in the container. Mount a folder there in docker-compose.override.yml."
            )

    def list_backups(self) -> list[RemoteFile]:
        self.ready()
        found = [
            RemoteFile(entry.name, entry.stat().st_size)
            for entry in self.folder.iterdir()
            if entry.is_file() and BACKUP_NAME.match(entry.name)
        ]
        return sorted(found, key=lambda item: item.name, reverse=True)

    def free_space(self) -> int | None:
        self.ready()
        return shutil.disk_usage(self.folder).free

    def open_write(self, name: str) -> BinaryIO:
        self.ready()
        return (self.folder / name).open("wb")

    def open_read(self, name: str) -> BinaryIO:
        return (self.folder / name).open("rb")

    def rename(self, old: str, new: str) -> None:
        (self.folder / old).replace(self.folder / new)

    def remove(self, name: str) -> None:
        (self.folder / name).unlink(missing_ok=True)

    def size_of(self, name: str) -> int | None:
        path = self.folder / name
        return path.stat().st_size if path.is_file() else None


def chunks(handle: BinaryIO) -> Iterator[bytes]:
    while block := handle.read(CHUNK):
        yield block


def checksum(handle: BinaryIO) -> str:
    digest = hashlib.sha256()
    for block in chunks(handle):
        digest.update(block)
    return digest.hexdigest()


def copy_to(destination: Destination, source: Path) -> str:
    """Copy a backup zip to the destination, in parts, then check it there; returns a short result."""
    name = source.name
    partial = f".{name}.partial"
    digest = hashlib.sha256()
    try:
        with source.open("rb") as reader, destination.open_write(partial) as writer:
            for block in chunks(reader):
                digest.update(block)
                writer.write(block)
        destination.rename(partial, name)
    except DestinationError:
        safe_remove(destination, partial)
        raise
    except Exception as error:  # noqa: BLE001 - every way a copy can fail ends the same: partial gone, a reason
        safe_remove(destination, partial)
        raise DestinationError(f"The copy failed: {error}") from error
    size = source.stat().st_size
    if destination.size_of(name) != size:
        safe_remove(destination, name)
        raise DestinationError("The copy has another size than the backup; it was removed.")
    with destination.open_read(name) as reader:
        if checksum(reader) != digest.hexdigest():
            safe_remove(destination, name)
            raise DestinationError("The copy does not match the backup (SHA-256); it was removed.")
    return f"Copied and checked ({size} bytes)."


def safe_remove(destination: Destination, name: str) -> None:
    try:
        destination.remove(name)
    except Exception:  # noqa: BLE001 - cleaning up after a failure must not hide the failure
        pass


def prune_destination(destination: Destination, keep: int) -> int:
    removed = 0
    for old in destination.list_backups()[max(keep, 1) :]:
        destination.remove(old.name)
        removed += 1
    return removed


def fetch_from(destination: Destination, name: str, target_folder: Path) -> Path:
    """Copy a backup from the destination into data/backups, checked by size, for the normal restore."""
    if not BACKUP_NAME.match(name):
        raise DestinationError("Unknown backup.")
    expected = destination.size_of(name)
    if expected is None:
        raise DestinationError(f"{name} is not at {destination.describe()}.")
    target_folder.mkdir(parents=True, exist_ok=True)
    target = target_folder / name
    if target.exists():
        raise DestinationError(f"{name} is already in YAAP's own backups.")
    partial = target_folder / f".{name}.fetch.partial"
    try:
        with destination.open_read(name) as reader, partial.open("wb") as writer:
            for block in chunks(reader):
                writer.write(block)
        if partial.stat().st_size != expected:
            raise DestinationError("The copy has another size than the backup at the destination.")
        partial.replace(target)
    except DestinationError:
        partial.unlink(missing_ok=True)
        raise
    except Exception as error:  # noqa: BLE001 - whatever the destination's reader or the disk raises is reported
        partial.unlink(missing_ok=True)
        raise DestinationError(f"The copy failed: {error}") from error
    return target
