"""Which GitHub token YAAP reads public and private repositories with."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.credentials.crypto import CredentialCipher, CredentialDecryptionError
from app.credentials.store import read_credential_value
from app.storage.models import Environment


def github_token_for(session: Session, cipher: CredentialCipher, environment_id: int | None) -> str | None:
    """The viewed environment's GitHub token; the global level uses the first environment that has one."""
    if environment_id is not None:
        ids = [environment_id]
    else:
        ids = list(session.scalars(select(Environment.id).order_by(Environment.id)))
    for current in ids:
        try:
            token = read_credential_value(session, cipher, current, "github_token", used_by="Skills from GitHub")
        except CredentialDecryptionError:
            token = None
        if token:
            return token
    return None
