"""Encryption of credentials at rest with the master key.

During a rotation the cipher also holds the previous key: it encrypts with the new key and decrypts
with either, so YAAP keeps working while every stored value is re-encrypted on start.
"""

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class CredentialDecryptionError(RuntimeError):
    """Raised when a stored credential cannot be decrypted, usually after the master key changed."""


class CredentialCipher:
    def __init__(self, master_key: bytes, previous_key: bytes | None = None) -> None:
        self._current = Fernet(master_key)
        self._fernet = MultiFernet([self._current, Fernet(previous_key)]) if previous_key else MultiFernet([self._current])
        self.rotating = previous_key is not None

    def encrypt(self, value: str) -> bytes:
        return self._fernet.encrypt(value.encode())

    def decrypt(self, token: bytes) -> str:
        try:
            return self._fernet.decrypt(token).decode()
        except InvalidToken as error:
            raise CredentialDecryptionError(
                "A stored credential cannot be decrypted with the current YAAP_MASTER_KEY. Enter it again in Settings."
            ) from error

    def uses_current_key(self, token: bytes) -> bool:
        try:
            self._current.decrypt(token)
            return True
        except InvalidToken:
            return False

    def rotate(self, token: bytes) -> bytes:
        """The same value encrypted with the current key. Raises CredentialDecryptionError for an unknown key."""
        try:
            return self._fernet.rotate(token)
        except InvalidToken as error:
            raise CredentialDecryptionError("A stored value cannot be decrypted with either master key.") from error
