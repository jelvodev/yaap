"""Master key rotation.

Put the new key in YAAP_MASTER_KEY and the old one in YAAP_MASTER_KEY_PREVIOUS, then restart. On start YAAP
re-encrypts every stored value with the new key; values it cannot decrypt with either key are counted and left alone.
Settings says when no value needs the old key any more, so YAAP_MASTER_KEY_PREVIOUS can be removed.
"""

import logging
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.credentials.crypto import CredentialCipher, CredentialDecryptionError

logger = logging.getLogger(__name__)

# Every table with encrypted values; the old ones stay until they are removed with approval.
ENCRYPTED = (("vault_entries", "encrypted_value"), ("credentials", "encrypted_value"), ("secrets", "encrypted_value"))


@dataclass
class KeyState:
    source: str = "environment"
    rotating: bool = False
    rotated: int = 0
    # Values that need the old key, and values neither key can read.
    on_previous: int = 0
    unreadable: int = 0


def rows(session: Session, table: str, column: str) -> list[tuple[int, bytes]]:
    return [(row[0], row[1]) for row in session.execute(text(f"SELECT id, {column} FROM {table}"))]  # noqa: S608


def rotate_all(session: Session, cipher: CredentialCipher) -> KeyState:
    """Re-encrypt every value that still uses the previous key, and commit."""
    state = KeyState(rotating=cipher.rotating)
    for table, column in ENCRYPTED:
        for row_id, token in rows(session, table, column):
            if cipher.uses_current_key(token):
                continue
            if not cipher.rotating:
                state.unreadable += 1
                continue
            try:
                new_token = cipher.rotate(token)
            except CredentialDecryptionError:
                state.unreadable += 1
                continue
            session.execute(text(f"UPDATE {table} SET {column} = :value WHERE id = :id"), {"value": new_token, "id": row_id})  # noqa: S608
            state.rotated += 1
    session.commit()
    if state.rotated:
        logger.info("Re-encrypted %s stored values with the new master key.", state.rotated)
    return state


def check_keys(session: Session, cipher: CredentialCipher) -> KeyState:
    """How the stored values stand against the keys, without changing anything."""
    state = KeyState(rotating=cipher.rotating)
    for table, column in ENCRYPTED:
        for _row_id, token in rows(session, table, column):
            if cipher.uses_current_key(token):
                continue
            try:
                cipher.decrypt(token)
                state.on_previous += 1
            except CredentialDecryptionError:
                state.unreadable += 1
    return state
