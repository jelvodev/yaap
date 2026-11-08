"""API tokens: one per purpose and client, kept in the vault, shown once, revocable.

A token looks like `yaap_<id>_<secret>`. It is a vault entry of kind `api_token` at Global: the value is encrypted
like every vault value, and `public_fields` hold the token's id, a SHA-256 hash of the whole token (to check a
presented token without decrypting anything), its purpose and what it is confined to. Removing the entry (in
Settings › Account › Access, or in Settings › Connections › Vault) revokes the token at once.

Purposes:
- `api`: the whole REST API, as a signed-in browser, for scripts. Not for managing the account or tokens.
- `mcp`: YAAP's tools as an MCP server for an outside client, confined to an environment (and optionally a project).
- `a2a`: YAAP as an A2A agent, confined the same way.

The routes for `mcp` and `a2a` check their token themselves with `require_api_token(purpose)`; the session gate in
`app/auth/gate.py` leaves the paths in TOKEN_PATHS to them.
"""

import hashlib
import hmac
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.credentials.crypto import CredentialCipher
from app.storage.models import Environment, Project, VaultEntry
from app.vault.store import VaultError, check_name, record_use, save_entry

API_TOKEN = "api_token"
PURPOSES: dict[str, str] = {
    "api": "REST API",
    "mcp": "MCP server",
    "a2a": "A2A agent",
}
# Purposes confined to an environment (and optionally a project).
CONFINED_PURPOSES = frozenset({"mcp", "a2a"})
# Path prefixes whose routes check a token themselves instead of a browser session: YAAP's MCP endpoint (per-run
# tokens of its own model processes, and outside clients) and the A2A agent.
TOKEN_PATHS: tuple[str, ...] = ("/mcp", "/mcp/outside", "/a2a", "/.well-known/agent-card.json")
TOKEN_PATTERN = re.compile(r"^yaap_([0-9a-f]{12})_([A-Za-z0-9_-]{20,100})$")
# last_used_at and the vault's use log are written at most once a minute per token.
TOUCH_EVERY = timedelta(minutes=1)


@dataclass(frozen=True)
class TokenGrant:
    """What a presented token allows: its vault entry, purpose and confinement."""

    token_id: int
    purpose: str
    environment_id: int
    project_id: int | None


@dataclass(frozen=True)
class ApiToken:
    """A live token as the session gate and Settings › Account › Access see it; environment_id is None for `api`."""

    entry_id: int
    name: str
    purpose: str
    environment_id: int | None
    project_id: int | None


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def token_of(entry: VaultEntry) -> ApiToken:
    fields = entry.public_fields or {}
    return ApiToken(
        entry_id=entry.id,
        name=entry.name,
        purpose=str(fields.get("purpose", "api")),
        environment_id=fields.get("environment_id"),
        project_id=fields.get("project_id"),
    )


def token_entries(session: Session) -> list[VaultEntry]:
    return list(session.scalars(select(VaultEntry).where(VaultEntry.kind == API_TOKEN).order_by(VaultEntry.name)))


def create_token(
    session: Session,
    cipher: CredentialCipher,
    *,
    name: str,
    purpose: str,
    environment_id: int | None = None,
    project_id: int | None = None,
    description: str = "",
    expires_at: datetime | None = None,
) -> tuple[VaultEntry, str]:
    """Make a token and store it in the vault. Returns the entry and the token, which is never shown again.
    Does not commit."""
    if purpose not in PURPOSES:
        raise VaultError(f"The purpose is one of {', '.join(PURPOSES)}.")
    name = check_name(name, agent_use=False)
    if any(entry.name == name for entry in token_entries(session)):
        raise VaultError(f"There is already a token named {name}; revoke it first or choose another name.")
    if purpose in CONFINED_PURPOSES:
        if environment_id is None or session.get(Environment, environment_id) is None:
            raise VaultError("Choose the environment the token is confined to.")
        if project_id is not None:
            project = session.get(Project, project_id)
            if project is None or project.environment_id != environment_id:
                raise VaultError("The project is not part of that environment.")
    elif environment_id is not None or project_id is not None:
        raise VaultError("A REST API token is not confined to an environment; it acts as the signed-in user.")
    token_id = secrets.token_hex(6)
    token = f"yaap_{token_id}_{secrets.token_urlsafe(32)}"
    entry = save_entry(
        session,
        cipher,
        environment_id=None,
        project_id=None,
        name=name,
        kind=API_TOKEN,
        value=token,
        agent_use=False,
        description=description or f"{PURPOSES[purpose]} token",
        expires_at=expires_at,
    )
    confined = purpose in CONFINED_PURPOSES
    entry.public_fields = {
        "token_id": token_id,
        "token_hash": digest(token),
        "purpose": purpose,
        "environment_id": environment_id if confined else None,
        "project_id": project_id if confined else None,
    }
    session.flush()
    return entry, token


def issue_api_token(
    session: Session,
    cipher: CredentialCipher,
    *,
    name: str,
    purpose: str,
    environment_id: int,
    project_id: int | None = None,
) -> str:
    """Create a token (stored in the vault, revocable) and return its value once. Commits."""
    _, token = create_token(
        session, cipher, name=name, purpose=purpose, environment_id=environment_id, project_id=project_id
    )
    session.commit()
    return token


def as_utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def verify_token(session: Session, presented: str, used_by: str | None = None) -> ApiToken | None:
    """The token a request presents, when it is a live token of this YAAP; records the use (and commits)."""
    match = TOKEN_PATTERN.match(presented or "")
    if match is None:
        return None
    token_id = match.group(1)
    for entry in token_entries(session):
        fields: dict[str, Any] = entry.public_fields or {}
        if fields.get("token_id") != token_id:
            continue
        if not hmac.compare_digest(str(fields.get("token_hash", "")), digest(presented)):
            return None
        moment = datetime.now(UTC)
        if entry.expires_at is not None and as_utc(entry.expires_at) <= moment:
            return None
        found = token_of(entry)
        if found.purpose in CONFINED_PURPOSES:
            # A confined token whose environment or project is gone confines to nothing.
            if found.environment_id is None or session.get(Environment, found.environment_id) is None:
                return None
            if found.project_id is not None and session.get(Project, found.project_id) is None:
                return None
        if entry.last_used_at is None or moment - as_utc(entry.last_used_at) >= TOUCH_EVERY:
            record_use(session, entry, used_by or f"{PURPOSES.get(found.purpose, found.purpose)} request")
            session.commit()
        return found
    return None


def bearer_value(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    if authorization[:7].lower() == "bearer ":
        return authorization[7:].strip()
    return None


def require_api_token(purpose: str) -> Callable[[Request], TokenGrant]:
    """FastAPI dependency factory: reads `Authorization: Bearer <token>` and returns a TokenGrant; 401 when the
    token is missing, unknown, revoked, expired or for another purpose."""

    def dependency(request: Request) -> TokenGrant:
        presented = bearer_value(request)
        challenge = {"WWW-Authenticate": "Bearer"}
        if not presented:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This endpoint needs an API token.", headers=challenge)
        with request.app.state.session_factory() as session:
            token = verify_token(session, presented, f"{PURPOSES.get(purpose, purpose)} request")
        if token is None or token.purpose != purpose or token.environment_id is None:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                f"This endpoint needs a valid API token for {PURPOSES.get(purpose, purpose)}.",
                headers=challenge,
            )
        return TokenGrant(
            token_id=token.entry_id,
            purpose=token.purpose,
            environment_id=token.environment_id,
            project_id=token.project_id,
        )

    return dependency


def revoke_token(session: Session, entry_id: int) -> bool:
    entry = session.get(VaultEntry, entry_id)
    if entry is None or entry.kind != API_TOKEN:
        return False
    session.delete(entry)
    return True
