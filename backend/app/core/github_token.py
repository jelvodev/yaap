"""The GitHub token a run's git tools use, read from the credentials of its environment or project."""

from sqlalchemy.orm import Session

from app.credentials.crypto import CredentialCipher, CredentialDecryptionError
from app.credentials.store import read_credential_value


def read_github_token(
    session: Session, cipher: CredentialCipher, environment_id: int, project_id: int | None, conversation_id: int
) -> str | None:
    try:
        return read_credential_value(
            session, cipher, environment_id, "github_token", project_id, "Git in a conversation", conversation_id
        )
    except CredentialDecryptionError:
        return None
