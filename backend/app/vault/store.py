"""The vault: one store for YAAP's own keys and the secrets the agent may use by name.

An entry lives at a level: global (no environment), an environment, or a project of it. YAAP's own keys (every kind
except generic) are used by YAAP itself and never offered to the agent; an entry with agent_use is a
`{{secret:NAME}}` placeholder (app/secrets/service.py). Values are encrypted with the master key, never returned by
the API, and every use is recorded without the value.

Resolving a key of a kind: the project's own entry of that kind, else the environment's default. Resolving
placeholders: project, then environment, then global, the nearest level winning by name.
"""

import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.credentials.crypto import CredentialCipher
from app.storage.models import VaultEntry, VaultUse

GENERIC = "generic"
# YAAP's own keys that belong to the whole installation, not to an environment: a network share for backups, and
# the API tokens.
GLOBAL_KINDS = frozenset({"smb_password", "api_token"})
# Placeholder names: what the agent writes in {{secret:NAME}}.
PLACEHOLDER_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
# Names of YAAP's own keys, such as "default", "personal" or "client-org".
KEY_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")
MAX_VALUE_CHARS = 16000
USES_KEPT = timedelta(days=90)
# A use by the same thing within this time updates the last record instead of adding one.
USE_COALESCE = timedelta(minutes=10)
EXPIRY_WARNING = timedelta(days=14)


class VaultError(Exception):
    """A vault action that cannot be done; the message is safe to show."""


def now() -> datetime:
    return datetime.now(UTC)


def level_of(entry: VaultEntry) -> str:
    return "project" if entry.project_id is not None else "environment" if entry.environment_id is not None else "global"


def at_level(query, environment_id: int | None, project_id: int | None):  # type: ignore[no-untyped-def]
    query = query.where(VaultEntry.environment_id.is_(None) if environment_id is None else VaultEntry.environment_id == environment_id)
    return query.where(VaultEntry.project_id.is_(None) if project_id is None else VaultEntry.project_id == project_id)


def check_name(name: str, agent_use: bool) -> str:
    name = name.strip()
    if agent_use and not PLACEHOLDER_NAME.match(name):
        raise VaultError("Use capital letters, digits and underscores, starting with a letter, for example DB_PASSWORD.")
    if not agent_use and not KEY_NAME.match(name):
        raise VaultError("Use at most 64 letters, digits, spaces, dots, dashes or underscores, for example personal.")
    return name


def find_entry(
    session: Session, environment_id: int | None, project_id: int | None, name: str, *, agent_use: bool, kind: str | None = None
) -> VaultEntry | None:
    """The entry with this name at exactly this level: a placeholder, or a key of the given kind."""
    query = at_level(select(VaultEntry), environment_id, project_id).where(
        VaultEntry.name == name, VaultEntry.agent_use.is_(agent_use)
    )
    if kind is not None:
        query = query.where(VaultEntry.kind == kind)
    return session.scalar(query)


def entries_of_kind(session: Session, environment_id: int, kind: str, project_id: int | None = None) -> list[VaultEntry]:
    query = at_level(select(VaultEntry), environment_id, project_id).where(
        VaultEntry.kind == kind, VaultEntry.agent_use.is_(False)
    )
    return list(session.scalars(query.order_by(VaultEntry.is_default.desc(), VaultEntry.name)))


def default_entry(session: Session, environment_id: int, kind: str, project_id: int | None = None) -> VaultEntry | None:
    """The key of this kind a project or environment uses: the project's own, else the environment's default."""
    if project_id is not None:
        own = entries_of_kind(session, environment_id, kind, project_id)
        if own:
            return own[0]
    entries = entries_of_kind(session, environment_id, kind)
    return entries[0] if entries else None


def make_default(session: Session, entry: VaultEntry) -> None:
    for other in entries_of_kind(session, entry.environment_id or 0, entry.kind, entry.project_id):
        other.is_default = other.id == entry.id
    entry.is_default = True


def save_entry(
    session: Session,
    cipher: CredentialCipher,
    *,
    environment_id: int | None,
    project_id: int | None,
    name: str,
    kind: str,
    value: str,
    agent_use: bool,
    description: str | None = None,
    expires_at: datetime | None = None,
    test_message: str | None = None,
) -> VaultEntry:
    """Create the entry, or replace the value of the one with this name at this level. Does not commit."""
    if not agent_use and kind == GENERIC:
        raise VaultError("A generic entry is a secret for the agent; YAAP's own keys have their own kind.")
    if agent_use and kind != GENERIC:
        raise VaultError("YAAP's own keys are never offered to the agent.")
    if not agent_use and environment_id is None and kind not in GLOBAL_KINDS:
        raise VaultError("YAAP's own keys belong to an environment.")
    if kind in GLOBAL_KINDS and environment_id is not None:
        raise VaultError("This kind of key belongs to Global.")
    name = check_name(name, agent_use)
    if not value:
        raise VaultError("The value is empty.")
    if len(value) > MAX_VALUE_CHARS:
        raise VaultError(f"Keep a value under {MAX_VALUE_CHARS} characters.")
    moment = now()
    entry = find_entry(session, environment_id, project_id, name, agent_use=agent_use, kind=None if agent_use else kind)
    if entry is None:
        first = not agent_use and environment_id is not None and not entries_of_kind(session, environment_id, kind, project_id)
        entry = VaultEntry(
            environment_id=environment_id,
            project_id=project_id,
            name=name,
            kind=kind,
            agent_use=agent_use,
            is_default=first,
            created_at=moment,
            public_fields={},
            description="",
            source="builtin",
        )
        session.add(entry)
    entry.encrypted_value = cipher.encrypt(value)
    entry.last_four = value[-4:] if not agent_use or len(value) >= 12 else ""
    entry.updated_at = moment
    if description is not None:
        entry.description = description.strip()
    if expires_at is not None:
        entry.expires_at = expires_at
    if test_message is not None:
        entry.last_tested_at = moment
        entry.last_test_result = test_message
    return entry


def remove_entry(session: Session, entry: VaultEntry) -> None:
    """Delete the entry; when it was the default of its kind, the next one of that kind becomes the default."""
    was_default = entry.is_default
    environment_id, project_id, kind = entry.environment_id, entry.project_id, entry.kind
    session.delete(entry)
    session.flush()
    if was_default and environment_id is not None:
        rest = entries_of_kind(session, environment_id, kind, project_id)
        if rest:
            rest[0].is_default = True


def record_use(session: Session, entry: VaultEntry, used_by: str, conversation_id: int | None = None) -> None:
    """Note that the entry was used; the caller commits."""
    moment = now()
    entry.last_used_at = moment
    same_conversation = (
        VaultUse.conversation_id.is_(None) if conversation_id is None else VaultUse.conversation_id == conversation_id
    )
    recent = session.scalar(
        select(VaultUse)
        .where(VaultUse.entry_id == entry.id, VaultUse.used_by == used_by[:200], same_conversation)
        .order_by(VaultUse.used_at.desc())
        .limit(1)
    )
    if recent is not None and moment - as_utc(recent.used_at) < USE_COALESCE:
        recent.used_at = moment
        return
    session.add(VaultUse(entry_id=entry.id, used_at=moment, used_by=used_by[:200], conversation_id=conversation_id))


def as_utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def prune_uses(session: Session) -> int:
    result = session.execute(delete(VaultUse).where(VaultUse.used_at < now() - USES_KEPT))
    session.commit()
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


_last_prune: list[datetime] = []


def prune_uses_daily(session: Session) -> None:
    """Called from the minute loop: removes uses older than 90 days, at most once a day."""
    if _last_prune and now() - _last_prune[0] < timedelta(days=1):
        return
    prune_uses(session)
    _last_prune[:] = [now()]


def read_value(session: Session, cipher: CredentialCipher, entry: VaultEntry, used_by: str | None = None, conversation_id: int | None = None) -> str:
    """The decrypted value; with used_by, the use is recorded and committed."""
    value = cipher.decrypt(entry.encrypted_value)
    if used_by:
        record_use(session, entry, used_by, conversation_id)
        session.commit()
    return value


def placeholder_entries(session: Session, environment_id: int, project_id: int | None) -> dict[str, VaultEntry]:
    """The secrets a run in this environment (and project) may use: global, then environment, then project."""
    query = select(VaultEntry).where(
        VaultEntry.agent_use.is_(True),
        (VaultEntry.environment_id.is_(None)) | (VaultEntry.environment_id == environment_id),
    )
    query = query.where(
        VaultEntry.project_id.is_(None) if project_id is None else (VaultEntry.project_id.is_(None)) | (VaultEntry.project_id == project_id)
    )
    found: dict[str, VaultEntry] = {}
    rank = {"global": 0, "environment": 1, "project": 2}
    for entry in session.scalars(query):
        current = found.get(entry.name)
        if current is None or rank[level_of(entry)] > rank[level_of(current)]:
            found[entry.name] = entry
    return dict(sorted(found.items()))


def expiry_state(entry: VaultEntry, moment: datetime | None = None) -> str | None:
    """expired, soon (within 14 days) or None."""
    if entry.expires_at is None:
        return None
    moment = moment or now()
    expires = as_utc(entry.expires_at)
    if expires <= moment:
        return "expired"
    if expires - moment <= EXPIRY_WARNING:
        return "soon"
    return None
