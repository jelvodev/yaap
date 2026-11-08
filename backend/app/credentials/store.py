"""YAAP's own keys per environment, stored in the vault. Values are encrypted and never returned
to callers outside the backend.

These functions work on the default key of a kind: the project's own entry of that kind when a project is given,
else the environment's default. The vault screen manages the other entries.
"""

from sqlalchemy.orm import Session

from app.credentials.crypto import CredentialCipher
from app.credentials.types import CredentialTestResult
from app.storage.models import VaultEntry
from app.vault.store import default_entry, now, read_value, save_entry

DEFAULT_NAME = "default"


def find_credential(session: Session, environment_id: int, kind: str, project_id: int | None = None) -> VaultEntry | None:
    return default_entry(session, environment_id, kind, project_id)


def save_credential(
    session: Session,
    cipher: CredentialCipher,
    environment_id: int,
    kind: str,
    value: str,
    test_result: CredentialTestResult,
) -> VaultEntry:
    """Replace the environment's default key of this kind, or add it as the first."""
    current = default_entry(session, environment_id, kind)
    entry = save_entry(
        session,
        cipher,
        environment_id=environment_id,
        project_id=None,
        name=current.name if current else DEFAULT_NAME,
        kind=kind,
        value=value,
        agent_use=False,
        test_message=test_result.message,
    )
    session.commit()
    return entry


def record_test_result(session: Session, credential: VaultEntry, test_result: CredentialTestResult) -> None:
    credential.last_tested_at = now()
    credential.last_test_result = test_result.message
    session.commit()


def read_credential_value(
    session: Session,
    cipher: CredentialCipher,
    environment_id: int,
    kind: str,
    project_id: int | None = None,
    used_by: str | None = None,
    conversation_id: int | None = None,
) -> str | None:
    """The key's value; with used_by the use is recorded in the vault's log."""
    entry = default_entry(session, environment_id, kind, project_id)
    if entry is None:
        return None
    return read_value(session, cipher, entry, used_by, conversation_id)
