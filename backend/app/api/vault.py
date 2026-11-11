"""The vault: YAAP's own keys and the agent's secrets in one place. Values are write-only."""

import json
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import CipherDependency, CredentialTypesDependency, SessionDependency
from app.auth.gate import request_identity
from app.auth.tokens import API_TOKEN, may_modify_vault_entry
from app.credentials.crypto import CredentialDecryptionError
from app.credentials.types import CredentialType
from app.secrets.service import PLACEHOLDER
from app.storage.models import Environment, Project, Setting, Target, VaultEntry, VaultUse
from app.vault.rotation import check_keys
from app.vault.store import (
    GENERIC,
    VaultError,
    as_utc,
    expiry_state,
    level_of,
    make_default,
    now,
    prune_uses,
    read_value,
    remove_entry,
    save_entry,
)

router = APIRouter(prefix="/vault", tags=["vault"])

SMB_PASSWORD = "smb_password"
SSH_KEY = "ssh_key"
GENERIC_LABEL = "Secret for the agent"
GENERIC_DESCRIPTION = "A value the agent uses by name as {{secret:NAME}} in commands, MCP servers and browser input."


class VaultKindOut(BaseModel):
    kind: str
    label: str
    description: str
    # YAAP can check the value with the service it belongs to.
    testable: bool
    agent_use: bool
    # Where an entry of this kind can be added: global, environment, project.
    levels: list[str]


class VaultEntryOut(BaseModel):
    id: int
    name: str
    kind: str
    kind_label: str
    level: Literal["global", "environment", "project"]
    environment_id: int | None
    project_id: int | None
    # Where the entry lives, for example "Work" or "Work › Website".
    level_name: str
    agent_use: bool
    is_default: bool
    # A nearer level has an entry the agent uses under the same name instead.
    hidden_by_nearer: bool
    last_four: str
    description: str
    expires_at: datetime | None
    expiry: Literal["expired", "soon"] | None
    last_used_at: datetime | None
    last_tested_at: datetime | None
    last_test_result: str | None
    updated_at: datetime


class VaultEntryIn(BaseModel):
    environment_id: int | None = None
    project_id: int | None = None
    name: str = Field(min_length=1, max_length=64)
    kind: str = Field(min_length=1, max_length=40)
    value: str = Field(min_length=1, max_length=16000)
    description: str = Field(default="", max_length=500)
    expires_at: datetime | None = None


class VaultEntryUpdate(BaseModel):
    value: str | None = Field(default=None, min_length=1, max_length=16000)
    description: str | None = Field(default=None, max_length=500)
    expires_at: datetime | None = None
    # True removes the expiry date.
    clear_expiry: bool = False
    # True makes this key the default of its kind at its level.
    make_default: bool = False


class VaultUseOut(BaseModel):
    used_at: datetime
    used_by: str
    conversation_id: int | None


class VaultDetailOut(BaseModel):
    entry: VaultEntryOut
    used_in: list[str]
    uses: list[VaultUseOut]


class VaultTestOut(BaseModel):
    ok: bool
    message: str


class MasterKeyOut(BaseModel):
    # environment: YAAP_MASTER_KEY; file: YAAP_MASTER_KEY_FILE.
    source: str
    # YAAP_MASTER_KEY_PREVIOUS is set.
    rotating: bool
    # Values re-encrypted with the new key on this start.
    rotated: int
    # Values that still need the previous key, and values neither key can read.
    on_previous: int
    unreadable: int
    message: str


def kinds(credential_types: dict[str, CredentialType]) -> dict[str, VaultKindOut]:
    found = {
        kind: VaultKindOut(
            kind=kind,
            label=item.label,
            description=item.description,
            testable=True,
            agent_use=False,
            levels=["environment", "project"],
        )
        for kind, item in credential_types.items()
    }
    found[SMB_PASSWORD] = VaultKindOut(
        kind=SMB_PASSWORD,
        label="Network share password",
        description=(
            "The password YAAP signs in with on a network share that receives backups (Settings › System › Backup)."
        ),
        testable=False,
        agent_use=False,
        levels=["global"],
    )
    found[SSH_KEY] = VaultKindOut(
        kind=SSH_KEY,
        label="SSH key of a target",
        description="The key YAAP signs in with on a computer from Settings › Targets. YAAP makes it with the target.",
        testable=False,
        agent_use=False,
        levels=[],
    )
    found[API_TOKEN] = VaultKindOut(
        kind=API_TOKEN,
        label="API token",
        description=(
            "A token for a script, an outside MCP client or an A2A client. YAAP makes it in "
            "Settings › Account › Access and shows it once; removing it here revokes it."
        ),
        testable=False,
        agent_use=False,
        levels=[],
    )
    found[GENERIC] = VaultKindOut(
        kind=GENERIC,
        label=GENERIC_LABEL,
        description=GENERIC_DESCRIPTION,
        testable=False,
        agent_use=True,
        levels=["global", "environment", "project"],
    )
    return found


def check_level(session: Session, environment_id: int | None, project_id: int | None) -> None:
    if project_id is not None and environment_id is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A project level needs its environment.")
    if environment_id is not None and session.get(Environment, environment_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Environment not found.")
    if project_id is not None:
        project = session.get(Project, project_id)
        if project is None or project.environment_id != environment_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found in this environment.")


def level_name(session: Session, entry: VaultEntry) -> str:
    if entry.environment_id is None:
        return "Global"
    environment = session.get(Environment, entry.environment_id)
    name = environment.name if environment else "?"
    if entry.project_id is not None:
        project = session.get(Project, entry.project_id)
        return f"{name} › {project.name if project else '?'}"
    return name


def entry_out(session: Session, entry: VaultEntry, labels: dict[str, VaultKindOut], hidden: bool = False) -> VaultEntryOut:
    return VaultEntryOut(
        id=entry.id,
        name=entry.name,
        kind=entry.kind,
        kind_label=labels[entry.kind].label if entry.kind in labels else entry.kind,
        level=level_of(entry),  # type: ignore[arg-type]
        environment_id=entry.environment_id,
        project_id=entry.project_id,
        level_name=level_name(session, entry),
        agent_use=entry.agent_use,
        is_default=entry.is_default,
        hidden_by_nearer=hidden,
        last_four=entry.last_four,
        description=entry.description or "",
        expires_at=entry.expires_at,
        expiry=expiry_state(entry),  # type: ignore[arg-type]
        last_used_at=entry.last_used_at,
        last_tested_at=entry.last_tested_at,
        last_test_result=entry.last_test_result,
        updated_at=entry.updated_at,
    )


def visible_entries(session: Session, environment_id: int | None, project_id: int | None) -> list[tuple[VaultEntry, bool]]:
    """The entries of a level and what it inherits, nearest first; with whether a nearer secret hides one."""
    query = select(VaultEntry)
    if environment_id is None:
        query = query.where(VaultEntry.environment_id.is_(None))
    else:
        query = query.where((VaultEntry.environment_id.is_(None)) | (VaultEntry.environment_id == environment_id))
        if project_id is None:
            query = query.where(VaultEntry.project_id.is_(None))
        else:
            query = query.where((VaultEntry.project_id.is_(None)) | (VaultEntry.project_id == project_id))
    rank = {"project": 0, "environment": 1, "global": 2}
    entries = sorted(session.scalars(query), key=lambda entry: (rank[level_of(entry)], entry.kind, entry.name))
    seen: set[str] = set()
    result = []
    for entry in entries:
        # YAAP's own keys of another environment level are not inherited; only secrets come from Global.
        hidden = entry.agent_use and entry.name in seen
        if entry.agent_use:
            seen.add(entry.name)
        result.append((entry, hidden))
    return result


def find(session: Session, entry_id: int) -> VaultEntry:
    entry = session.get(VaultEntry, entry_id)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such vault entry.")
    return entry


def servers_of(value: Any) -> list[tuple[str, Any]]:
    if isinstance(value, dict):
        return [(str(name), config) for name, config in value.items()]
    if isinstance(value, list):
        return [(str(item.get("name", "?")), item) for item in value if isinstance(item, dict)]
    return []


def where_used(session: Session, entry: VaultEntry, labels: dict[str, VaultKindOut]) -> list[str]:
    """Where the entry is used now: as a default key, or by name in MCP server settings."""
    places = []
    if entry.kind == API_TOKEN:
        purpose = (entry.public_fields or {}).get("purpose", "api")
        return [{"api": "The REST API", "mcp": "YAAP's MCP server", "a2a": "YAAP's A2A agent"}.get(purpose, purpose)]
    if entry.kind == SMB_PASSWORD:
        for row in session.scalars(select(Setting).where(Setting.key == "backup_destinations")):
            for item in row.value if isinstance(row.value, list) else []:
                if isinstance(item, dict) and item.get("password_entry") == entry.name:
                    places.append(f"Backup destination {item.get('name', '?')}")
        return places
    if entry.kind == SSH_KEY:
        return [f"Target {target.name}" for target in session.scalars(select(Target).where(Target.key_entry_id == entry.id))]
    if not entry.agent_use:
        label = labels[entry.kind].label if entry.kind in labels else entry.kind
        if entry.project_id is not None:
            places.append(f"{label} of the project {level_name(session, entry)}")
        elif entry.is_default:
            places.append(f"Default {label} of {level_name(session, entry)}, also for its projects without their own")
        return places
    for row in session.scalars(select(Setting).where(Setting.key == "mcp_servers")):
        for name, config in servers_of(row.value):
            if entry.name in {match.group(1) for match in PLACEHOLDER.finditer(json.dumps(config))}:
                places.append(f"MCP server {name}")
    return places


async def test_value(entry: VaultEntry, value: str, credential_types: dict[str, CredentialType]) -> VaultTestOut | None:
    credential_type = credential_types.get(entry.kind)
    if credential_type is None:
        return None
    result = await credential_type.test(value)
    return VaultTestOut(ok=result.ok, message=result.message)


@router.get("/master-key", response_model=MasterKeyOut)
def master_key_state(request: Request, session: SessionDependency, cipher: CipherDependency) -> MasterKeyOut:
    """Where the master key comes from and, during a rotation, whether the previous key can be removed."""
    state = check_keys(session, cipher)
    rotation = getattr(request.app.state, "key_rotation", None)
    rotated = rotation.rotated if rotation is not None else 0
    if state.unreadable:
        message = (
            f"{state.unreadable} stored value(s) cannot be read with the master key. They were stored with another "
            "key: put that key in YAAP_MASTER_KEY_PREVIOUS and restart, or enter the values again."
        )
    elif cipher.rotating and state.on_previous == 0:
        message = (
            f"Every value uses the new master key ({rotated} re-encrypted on start). Remove YAAP_MASTER_KEY_PREVIOUS "
            "from .env and restart YAAP."
        )
    elif cipher.rotating:
        message = f"{state.on_previous} value(s) still use the previous key; restart YAAP to re-encrypt them."
    else:
        message = "Every stored value uses the master key."
    return MasterKeyOut(
        source=getattr(request.app.state, "master_key_source", "environment"),
        rotating=cipher.rotating,
        rotated=rotated,
        on_previous=state.on_previous,
        unreadable=state.unreadable,
        message=message,
    )


@router.get("/kinds", response_model=list[VaultKindOut])
def list_kinds(credential_types: CredentialTypesDependency) -> list[VaultKindOut]:
    return list(kinds(credential_types).values())


@router.get("/entries", response_model=list[VaultEntryOut])
def list_entries(
    session: SessionDependency,
    credential_types: CredentialTypesDependency,
    environment_id: Annotated[int | None, Query()] = None,
    project_id: Annotated[int | None, Query()] = None,
) -> list[VaultEntryOut]:
    """The entries of a level (no environment: Global) and the secrets it inherits."""
    check_level(session, environment_id, project_id)
    labels = kinds(credential_types)
    return [entry_out(session, entry, labels, hidden) for entry, hidden in visible_entries(session, environment_id, project_id)]


@router.get("/attention", response_model=list[VaultEntryOut])
def entries_needing_attention(session: SessionDependency, credential_types: CredentialTypesDependency) -> list[VaultEntryOut]:
    """Every entry that expired or expires within 14 days, for the header."""
    labels = kinds(credential_types)
    entries = session.scalars(select(VaultEntry).where(VaultEntry.expires_at.is_not(None)).order_by(VaultEntry.expires_at))
    return [entry_out(session, entry, labels) for entry in entries if expiry_state(entry) is not None]


@router.post("/entries", response_model=VaultEntryOut, status_code=status.HTTP_201_CREATED)
async def create_entry(
    payload: VaultEntryIn, session: SessionDependency, cipher: CipherDependency, credential_types: CredentialTypesDependency
) -> VaultEntryOut:
    """Add an entry, or replace the value of the one with this name at this level. A key is tested first."""
    check_level(session, payload.environment_id, payload.project_id)
    labels = kinds(credential_types)
    if payload.kind not in labels:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown kind.")
    if payload.kind == API_TOKEN:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "YAAP makes API tokens itself, in Settings › Account › Access."
        )
    value = payload.value.strip() if payload.kind != GENERIC else payload.value
    test_message = None
    if payload.kind in credential_types:
        result = await credential_types[payload.kind].test(value)
        if not result.ok:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"The key was not saved: {result.message}")
        test_message = result.message
    try:
        entry = save_entry(
            session,
            cipher,
            environment_id=payload.environment_id,
            project_id=payload.project_id,
            name=payload.name,
            kind=payload.kind,
            value=value,
            agent_use=payload.kind == GENERIC,
            description=payload.description,
            expires_at=payload.expires_at,
            test_message=test_message,
        )
    except VaultError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    session.commit()
    return entry_out(session, entry, labels)


@router.get("/entries/{entry_id}", response_model=VaultDetailOut)
def get_entry(entry_id: int, session: SessionDependency, credential_types: CredentialTypesDependency) -> VaultDetailOut:
    """The entry, where it is used, and its use log of the last 90 days (newest first)."""
    prune_uses(session)
    entry = find(session, entry_id)
    labels = kinds(credential_types)
    uses = session.scalars(
        select(VaultUse).where(VaultUse.entry_id == entry.id).order_by(VaultUse.used_at.desc()).limit(200)
    )
    return VaultDetailOut(
        entry=entry_out(session, entry, labels),
        used_in=where_used(session, entry, labels),
        uses=[VaultUseOut(used_at=use.used_at, used_by=use.used_by, conversation_id=use.conversation_id) for use in uses],
    )


@router.patch("/entries/{entry_id}", response_model=VaultEntryOut)
async def update_entry(
    entry_id: int,
    payload: VaultEntryUpdate,
    request: Request,
    session: SessionDependency,
    cipher: CipherDependency,
    credential_types: CredentialTypesDependency,
) -> VaultEntryOut:
    """Replace the value (a key is tested first), change the description or expiry, or make a key the default."""
    entry = find(session, entry_id)
    identity = request_identity(request)
    if not may_modify_vault_entry(identity.method if identity else None, entry.kind):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "A browser session is required to manage API tokens.")
    labels = kinds(credential_types)
    if payload.value is not None and entry.kind == API_TOKEN:
        raise HTTPException(status.HTTP_409_CONFLICT, "An API token cannot be replaced; make a new one and revoke this one.")
    if payload.value is not None:
        value = payload.value.strip() if not entry.agent_use else payload.value
        result = await test_value(entry, value, credential_types)
        if result is not None and not result.ok:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"The key was not saved: {result.message}")
        entry.encrypted_value = cipher.encrypt(value)
        entry.last_four = value[-4:] if not entry.agent_use or len(value) >= 12 else ""
        entry.updated_at = now()
        if result is not None:
            entry.last_tested_at, entry.last_test_result = now(), result.message
    if payload.description is not None:
        entry.description = payload.description.strip()
    if payload.clear_expiry:
        entry.expires_at = None
    elif payload.expires_at is not None:
        entry.expires_at = as_utc(payload.expires_at)
    if payload.make_default:
        if entry.agent_use:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Only YAAP's own keys have a default.")
        make_default(session, entry)
    session.commit()
    return entry_out(session, entry, labels)


@router.post("/entries/{entry_id}/test", response_model=VaultTestOut)
async def test_entry(
    entry_id: int, session: SessionDependency, cipher: CipherDependency, credential_types: CredentialTypesDependency
) -> VaultTestOut:
    entry = find(session, entry_id)
    if entry.kind not in credential_types:
        raise HTTPException(status.HTTP_409_CONFLICT, "This kind of entry has no test.")
    try:
        value = read_value(session, cipher, entry)
    except CredentialDecryptionError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    result = await test_value(entry, value, credential_types)
    assert result is not None
    entry.last_tested_at, entry.last_test_result = now(), result.message
    session.commit()
    return result


@router.delete("/entries/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_entry(entry_id: int, session: SessionDependency, request: Request) -> Response:
    entry = find(session, entry_id)
    identity = request_identity(request)
    if not may_modify_vault_entry(identity.method if identity else None, entry.kind):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "A browser session is required to manage API tokens.")
    remove_entry(session, entry)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
