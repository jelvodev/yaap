"""Preview tokens: short, signed, scoped to one process and its port.

Two kinds, the same format. An *open* token is minted by an authenticated YAAP API call, lives a few minutes and is
used once: the preview listener exchanges it for a *session* token in a cookie of the preview's own, which lasts until
the process ends (at most SESSION_SECONDS). The key is random per YAAP start and kept only in memory, so a restart
(which ends every process) also ends every preview; nothing is stored, so no migration is needed.

Format: base64url("<kind>:<process>:<port>:<expires>:<nonce>") "." base64url(HMAC-SHA256 of that part).
"""

import base64
import binascii
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Literal

TokenKind = Literal["open", "session"]
TOKEN_KINDS: dict[str, TokenKind] = {"open": "open", "session": "session"}
OPEN_SECONDS = 120
SESSION_SECONDS = 12 * 60 * 60
KEY_BYTES = 32
MAX_TOKEN_CHARS = 512


@dataclass(frozen=True)
class PreviewGrant:
    """What a token allows: one kind, one process on one port, until `expires` (Unix seconds)."""

    kind: TokenKind
    process_id: int
    port: int
    expires: int
    nonce: str


def new_key() -> bytes:
    return secrets.token_bytes(KEY_BYTES)


def new_grant(kind: TokenKind, process_id: int, port: int, now: float, seconds: int) -> PreviewGrant:
    return PreviewGrant(kind, process_id, port, int(now) + seconds, secrets.token_urlsafe(12))


def encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def signature(key: bytes, payload: str) -> str:
    return encode(hmac.new(key, payload.encode("ascii"), hashlib.sha256).digest())


def mint(key: bytes, grant: PreviewGrant) -> str:
    payload = encode(f"{grant.kind}:{grant.process_id}:{grant.port}:{grant.expires}:{grant.nonce}".encode("ascii"))
    return f"{payload}.{signature(key, payload)}"


def parse_payload(payload: str) -> PreviewGrant | None:
    try:
        fields = decode(payload).decode("ascii").split(":")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    if len(fields) != 5:
        return None
    kind, process, port, expires, nonce = fields
    found = TOKEN_KINDS.get(kind)
    if found is None or not (process.isdigit() and port.isdigit() and expires.isdigit()) or not nonce:
        return None
    number = int(port)
    if not 1 <= number <= 65535:
        return None
    return PreviewGrant(found, int(process), number, int(expires), nonce)


def read(key: bytes, token: str | None, kind: TokenKind, now: float) -> PreviewGrant | None:
    """The grant of a token of this kind when its signature holds and it has not expired; None otherwise."""
    if not token or len(token) > MAX_TOKEN_CHARS or token.count(".") != 1:
        return None
    payload, signed = token.split(".")
    if not hmac.compare_digest(signed.encode("ascii", "replace"), signature(key, payload).encode("ascii")):
        return None
    grant = parse_payload(payload)
    if grant is None or grant.kind != kind or grant.expires <= now:
        return None
    return grant
