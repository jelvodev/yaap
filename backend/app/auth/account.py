"""The local account and the browser sessions.

YAAP has one account: a password, stored as an Argon2id hash. A browser that signs in gets a session: a random value
in an HttpOnly cookie and a CSRF token in a second cookie that the page reads and sends back in a header with every
request that changes something. Only SHA-256 hashes of both values are stored, so a copy of the database signs nobody
in.
"""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.storage.models import Account, AuthSession

ACCOUNT_ID = 1
MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 1024
# A session ends after 30 days without use.
SESSION_IDLE = timedelta(days=30)
# last_seen_at is written at most once a minute per session.
TOUCH_EVERY = timedelta(minutes=1)


class AccountError(Exception):
    """A password that cannot be set or used; the message is safe to show."""


def default_hasher() -> PasswordHasher:
    """Argon2id with argon2-cffi's defaults (RFC 9106's second recommendation: 3 passes, 64 MiB, 4 lanes)."""
    return PasswordHasher()


def now() -> datetime:
    return datetime.now(UTC)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def same(value: str, stored_hash: str) -> bool:
    return hmac.compare_digest(digest(value), stored_hash)


def check_new_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AccountError(f"Use at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise AccountError(f"Use at most {MAX_PASSWORD_LENGTH} characters.")
    if password.strip() != password:
        raise AccountError("The password starts or ends with a space; leave those out.")


def password_is_set(session: Session) -> bool:
    return session.get(Account, ACCOUNT_ID) is not None


def set_password(session: Session, hasher: PasswordHasher, password: str) -> None:
    """Set or replace the password. Does not commit."""
    check_new_password(password)
    account = session.get(Account, ACCOUNT_ID)
    moment = now()
    if account is None:
        account = Account(id=ACCOUNT_ID, created_at=moment)
        session.add(account)
    account.password_hash = hasher.hash(password)
    account.updated_at = moment


def check_password(session: Session, hasher: PasswordHasher, password: str) -> bool:
    """Whether the password is right. A hash made with older parameters is replaced (the caller commits)."""
    account = session.get(Account, ACCOUNT_ID)
    if account is None or not password or len(password) > MAX_PASSWORD_LENGTH:
        return False
    try:
        hasher.verify(account.password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    if hasher.check_needs_rehash(account.password_hash):
        account.password_hash = hasher.hash(password)
    return True


@dataclass(frozen=True)
class NewSession:
    id: str
    token: str
    csrf: str
    expires_at: datetime


def start_session(session: Session, user_agent: str = "") -> NewSession:
    """A new browser session. Does not commit."""
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    moment = now()
    record = AuthSession(
        id=digest(token),
        csrf_hash=digest(csrf),
        created_at=moment,
        last_seen_at=moment,
        expires_at=moment + SESSION_IDLE,
        user_agent=user_agent[:200],
    )
    session.add(record)
    return NewSession(record.id, token, csrf, record.expires_at)


def as_utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def find_session(session: Session, token: str) -> AuthSession | None:
    """The live session of a cookie value; an expired one is removed. Keeps an active session alive."""
    if not token or len(token) > 200:
        return None
    record = session.get(AuthSession, digest(token))
    if record is None:
        return None
    moment = now()
    if as_utc(record.expires_at) <= moment:
        session.delete(record)
        session.commit()
        return None
    if moment - as_utc(record.last_seen_at) >= TOUCH_EVERY:
        record.last_seen_at = moment
        record.expires_at = moment + SESSION_IDLE
        session.commit()
    return record


def end_session(session: Session, session_id: str) -> None:
    session.execute(delete(AuthSession).where(AuthSession.id == session_id))


def end_other_sessions(session: Session, keep_id: str | None = None) -> int:
    """End every session except keep_id (all of them without one). Does not commit."""
    query = delete(AuthSession)
    if keep_id is not None:
        query = query.where(AuthSession.id != keep_id)
    return int(session.execute(query).rowcount or 0)  # type: ignore[attr-defined]


def session_count(session: Session) -> int:
    return len(session.scalars(select(AuthSession.id)).all())
