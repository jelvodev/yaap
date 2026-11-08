"""How YAAP signs in on a target: its stored row and its private key from the vault, as SSH settings."""

from sqlalchemy.orm import Session

from app.credentials.crypto import CredentialCipher, CredentialDecryptionError
from app.storage.models import Target, VaultEntry
from app.targets.base import TargetError
from app.targets.ssh import SshSettings
from app.vault.store import read_value


def settings_for(
    session: Session, cipher: CredentialCipher, target: Target, used_by: str, conversation_id: int | None = None
) -> SshSettings:
    entry = session.get(VaultEntry, target.key_entry_id) if target.key_entry_id else None
    if entry is None:
        raise TargetError(f"The key of the target {target.name} is gone from the vault; remove the target and add it again.")
    try:
        private_key = read_value(session, cipher, entry, used_by=used_by, conversation_id=conversation_id)
    except CredentialDecryptionError as error:
        raise TargetError(str(error)) from error
    return SshSettings(
        host=target.host,
        port=target.port,
        user=target.user,
        private_key=private_key,
        host_key=target.host_key,
        system=target.system,
        shell=target.shell,
        label=target.name,
    )
