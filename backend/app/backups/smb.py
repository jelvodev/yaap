"""A network share as a backup destination, through smbprotocol: SMB 2 and 3, with encryption
required unless it is switched off for that destination.

YAAP connects to the share itself, so nothing has to be mounted on the computer; the password is a vault entry of
kind smb_password at the global level, named by the destination. Files are streamed in parts, so a large backup does
not fill memory.
"""

from typing import Any, BinaryIO

import smbclient
from smbprotocol.exceptions import SMBException
from sqlalchemy.orm import Session

from app.backups.destinations import DestinationConfig, DestinationError, RemoteFile
from app.core.backup import BACKUP_NAME
from app.credentials.crypto import CredentialDecryptionError
from app.vault.store import find_entry, read_value

PASSWORD_KIND = "smb_password"
CONNECT_SECONDS = 15
FAILURES = (SMBException, OSError, ValueError)


class SmbDestination:
    def __init__(self, config: DestinationConfig, password: str | None) -> None:
        self.config = config
        self.password = password
        self.connected = False

    @classmethod
    def from_config(cls, config: DestinationConfig, session: Session, cipher: Any) -> "SmbDestination":
        password = None
        if config.password_entry:
            entry = find_entry(session, None, None, config.password_entry, agent_use=False, kind=PASSWORD_KIND)
            if entry is None:
                raise DestinationError(
                    f"{config.name}: the vault has no network share password named {config.password_entry} at Global."
                )
            try:
                password = read_value(session, cipher, entry, used_by=f"Backup destination {config.name}")
            except CredentialDecryptionError as error:
                raise DestinationError(f"{config.name}: {error}") from error
        return cls(config, password)

    def describe(self) -> str:
        folder = "\\" + self.config.folder.replace("/", "\\") if self.config.folder else ""
        return f"\\\\{self.config.server}\\{self.config.share}{folder}"

    def path(self, name: str = "") -> str:
        parts = [self.config.server, self.config.share, *[part for part in self.config.folder.split("/") if part]]
        if name:
            parts.append(name)
        return "\\\\" + "\\".join(parts)

    def connect(self) -> None:
        if self.connected:
            return
        user = self.config.user
        if user and self.config.domain:
            user = f"{self.config.domain}\\{user}"
        try:
            smbclient.register_session(
                self.config.server,
                username=user or None,
                password=self.password,
                encrypt=self.config.require_encryption,
                connection_timeout=CONNECT_SECONDS,
            )
            if self.config.folder:
                smbclient.makedirs(self.path(), exist_ok=True)
        except FAILURES as error:
            raise DestinationError(f"{self.describe()} cannot be reached: {error}") from error
        self.connected = True

    def list_backups(self) -> list[RemoteFile]:
        self.connect()
        try:
            found = [
                RemoteFile(entry.name, entry.stat().st_size)
                for entry in smbclient.scandir(self.path())
                if entry.is_file() and BACKUP_NAME.match(entry.name)
            ]
        except FAILURES as error:
            raise DestinationError(f"{self.describe()} cannot be listed: {error}") from error
        return sorted(found, key=lambda item: item.name, reverse=True)

    def free_space(self) -> int | None:
        self.connect()
        try:
            return int(smbclient.stat_volume(self.path()).caller_available_size)
        except FAILURES:
            return None

    def open_write(self, name: str) -> BinaryIO:
        self.connect()
        try:
            return smbclient.open_file(self.path(name), mode="wb")  # type: ignore[return-value]
        except FAILURES as error:
            raise DestinationError(f"{self.describe()} cannot be written: {error}") from error

    def open_read(self, name: str) -> BinaryIO:
        self.connect()
        try:
            return smbclient.open_file(self.path(name), mode="rb")  # type: ignore[return-value]
        except FAILURES as error:
            raise DestinationError(f"{name} cannot be read at {self.describe()}: {error}") from error

    def rename(self, old: str, new: str) -> None:
        self.connect()
        try:
            smbclient.replace(self.path(old), self.path(new))
        except FAILURES as error:
            raise DestinationError(f"The copy cannot be renamed at {self.describe()}: {error}") from error

    def remove(self, name: str) -> None:
        self.connect()
        try:
            smbclient.remove(self.path(name))
        except FileNotFoundError:
            return
        except FAILURES as error:
            raise DestinationError(f"{name} cannot be removed at {self.describe()}: {error}") from error

    def size_of(self, name: str) -> int | None:
        self.connect()
        try:
            return int(smbclient.stat(self.path(name)).st_size)
        except FAILURES:
            return None
