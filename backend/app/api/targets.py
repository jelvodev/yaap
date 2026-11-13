"""Targets: machines over SSH the agent can work on, such as the computer that runs YAAP."""

import re
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import CipherDependency, EnvironmentDependency, SessionDependency
from app.credentials.crypto import CredentialCipher
from app.storage.models import Conversation, Target, VaultEntry
from app.targets.base import TargetError
from app.targets.service import (
    SSH_KEY,
    check_folders,
    host_fingerprint,
    settings_for,
    target_folder_problem,
)
from app.targets.setup import ADMINISTRATOR, STANDARD, windows_setup_script
from app.targets.ssh import CONNECTIONS, HostKeyUnconfirmed, fingerprint, new_key_pair, probe
from app.vault.store import remove_entry, save_entry

router = APIRouter(tags=["targets"])

HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,253}$")
USER = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


class TargetOut(BaseModel):
    id: int
    environment_id: int
    name: str
    host: str
    port: int
    user: str
    system: str
    shell: str
    rights: str
    folders: list[str]
    public_key: str
    # Confirmed, or seen but waiting for the user's confirmation.
    host_fingerprint: str | None
    pending_fingerprint: str | None
    allow_full_access: bool
    whole_computer: bool
    last_tested_at: datetime | None
    last_test_result: str | None
    # new (never connected), confirm (a host key waits), ready, or problem (the last test failed).
    state: Literal["new", "confirm", "ready", "problem"]


class TargetIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    host: str = Field(default="host.docker.internal", min_length=1, max_length=255)
    port: int = Field(default=22, ge=1, le=65535)
    user: str = Field(default="yaap", min_length=1, max_length=80)
    folders: list[str] = Field(default_factory=list, max_length=20)


class TargetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    host: str | None = Field(default=None, min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    user: str | None = Field(default=None, min_length=1, max_length=80)
    folders: list[str] | None = Field(default=None, max_length=20)
    allow_full_access: bool | None = None
    whole_computer: bool | None = None
    # Switching full access on must say so explicitly, after the screen named the user's rights.
    confirm_full_access: bool = False


class HostKeyIn(BaseModel):
    fingerprint: str = Field(min_length=10, max_length=120)


def public_key_of(session: Session, target: Target) -> str:
    entry = session.get(VaultEntry, target.key_entry_id) if target.key_entry_id else None
    return str((entry.public_fields or {}).get("public_key", "")) if entry else ""


def target_out(session: Session, target: Target) -> TargetOut:
    if target.pending_host_key:
        state = "confirm"
    elif target.last_test_result and target.last_test_result.startswith("Problem"):
        state = "problem"
    elif not target.host_key or target.last_tested_at is None:
        state = "new"
    else:
        state = "ready"
    return TargetOut(
        id=target.id,
        environment_id=target.environment_id,
        name=target.name,
        host=target.host,
        port=target.port,
        user=target.user,
        system=target.system,
        shell=target.shell,
        rights=target.rights,
        folders=list(target.folders or []),
        public_key=public_key_of(session, target),
        host_fingerprint=host_fingerprint(target.host_key),
        pending_fingerprint=host_fingerprint(target.pending_host_key),
        allow_full_access=target.allow_full_access,
        whole_computer=target.whole_computer,
        last_tested_at=target.last_tested_at,
        last_test_result=target.last_test_result,
        state=state,  # type: ignore[arg-type]  # a plain string here; the schema narrows it
    )


def check_input(host: str | None, user: str | None, folders: list[str] | None) -> list[str] | None:
    if host is not None and not HOST.match(host.strip()):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Use a host name or an IP address, such as host.docker.internal.")
    if user is not None and not USER.match(user.strip()):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Use a user name of letters, digits, dots, dashes or underscores.")
    if folders is None:
        return None
    cleaned = [folder.strip() for folder in folders if folder.strip()]
    for folder in cleaned:
        if problem := target_folder_problem(folder):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, problem)
    return cleaned


def find_target(session: Session, target_id: int) -> Target:
    target = session.get(Target, target_id)
    if target is None or target.kind != "ssh":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such target.")
    return target


@router.get("/environments/{environment_id}/targets", response_model=list[TargetOut])
def list_targets(environment: EnvironmentDependency, session: SessionDependency) -> list[TargetOut]:
    # A project's sandbox is a target too, but shown in its project, not here.
    targets = session.scalars(
        select(Target).where(Target.environment_id == environment.id, Target.kind == "ssh").order_by(Target.name)
    )
    return [target_out(session, target) for target in targets]


@router.post("/environments/{environment_id}/targets", response_model=TargetOut, status_code=status.HTTP_201_CREATED)
def create_target(
    payload: TargetIn, environment: EnvironmentDependency, session: SessionDependency, cipher: CipherDependency
) -> TargetOut:
    """Add a target with a new ed25519 key; the private key goes into the vault, the public key is shown."""
    folders = check_input(payload.host, payload.user, payload.folders) or []
    name = payload.name.strip()
    if session.scalar(select(Target).where(Target.environment_id == environment.id, Target.name == name, Target.kind == "ssh")):
        raise HTTPException(status.HTTP_409_CONFLICT, "A target with this name exists in this environment.")
    private_key, public_key = new_key_pair(f"yaap-{environment.slug}-{name}")
    entry = save_entry(
        session,
        cipher,
        environment_id=environment.id,
        project_id=None,
        name=f"target {name}"[:64],
        kind=SSH_KEY,
        value=private_key,
        agent_use=False,
        description=f"The key YAAP signs in with on the target {name}.",
    )
    entry.public_fields = {"public_key": public_key}
    entry.last_four = ""
    now = datetime.now(UTC)
    target = Target(
        environment_id=environment.id,
        name=name,
        host=payload.host.strip(),
        port=payload.port,
        user=payload.user.strip(),
        folders=folders,
        created_at=now,
        updated_at=now,
    )
    session.add(target)
    session.flush()
    target.key_entry_id = entry.id
    session.commit()
    return target_out(session, target)


@router.patch("/targets/{target_id}", response_model=TargetOut)
def update_target(target_id: int, payload: TargetUpdate, session: SessionDependency) -> TargetOut:
    target = find_target(session, target_id)
    folders = check_input(payload.host, payload.user, payload.folders)
    if payload.name is not None:
        target.name = payload.name.strip()
    if payload.host is not None or payload.port is not None:
        changed = (payload.host or target.host).strip() != target.host or (payload.port or target.port) != target.port
        target.host, target.port = (payload.host or target.host).strip(), payload.port or target.port
        if changed:
            # Another machine: its host key must be confirmed again.
            target.host_key, target.pending_host_key, target.last_tested_at = None, None, None
    if payload.user is not None:
        target.user = payload.user.strip()
    if folders is not None:
        target.folders = folders
    if payload.allow_full_access is not None:
        if payload.allow_full_access and not target.allow_full_access and not payload.confirm_full_access:
            raise HTTPException(status.HTTP_409_CONFLICT, "Confirm that conversations here may run every tool without asking.")
        target.allow_full_access = payload.allow_full_access
    if payload.whole_computer is not None:
        target.whole_computer = payload.whole_computer
    target.updated_at = datetime.now(UTC)
    session.commit()
    CONNECTIONS.close(f"target-{target.id}")
    return target_out(session, target)


@router.delete("/targets/{target_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_target(target_id: int, session: SessionDependency) -> Response:
    """Remove the target and its key; conversations that used it go back to the container."""
    target = find_target(session, target_id)
    entry = session.get(VaultEntry, target.key_entry_id) if target.key_entry_id else None
    CONNECTIONS.close(f"target-{target.id}")
    for conversation in session.scalars(select(Conversation).where(Conversation.target_id == target.id)):
        conversation.target_id = None
    session.delete(target)
    if entry is not None:
        remove_entry(session, entry)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def run_test(session: Session, cipher: CredentialCipher, target: Target) -> TargetOut:
    settings = settings_for(session, cipher, target, used_by=f"Test of the target {target.name}")
    now = datetime.now(UTC)
    try:
        found = await probe(settings)
    except HostKeyUnconfirmed as unconfirmed:
        target.pending_host_key = unconfirmed.key
        target.last_tested_at, target.last_test_result = now, f"Confirm the host key {unconfirmed.fingerprint}."
        session.commit()
        return target_out(session, target)
    except TargetError as error:
        target.last_tested_at, target.last_test_result = now, f"Problem: {error}"
        session.commit()
        return target_out(session, target)
    target.system, target.shell, target.rights = found["system"], found["shell"], found["rights"]
    settings.system, settings.shell = target.system, target.shell
    missing = await check_folders(settings, list(target.folders or []))
    system = {"windows": "Windows", "linux": "Linux", "macos": "macOS"}.get(target.system, target.system)
    summary = f"Connected: {system}, {target.rights} user, commands in {target.shell}."
    if missing:
        summary = f"Problem: {summary} These folders are missing: {', '.join(missing)}."
    target.last_tested_at, target.last_test_result = now, summary
    session.commit()
    CONNECTIONS.close(f"target-{target.id}")
    return target_out(session, target)


@router.post("/targets/{target_id}/test", response_model=TargetOut)
async def test_target(target_id: int, session: SessionDependency, cipher: CipherDependency) -> TargetOut:
    """Connect, ask to confirm a new host key, detect the system and the user's rights, and check the folders."""
    return await run_test(session, cipher, find_target(session, target_id))


@router.post("/targets/{target_id}/host-key", response_model=TargetOut)
async def confirm_host_key(
    target_id: int, payload: HostKeyIn, session: SessionDependency, cipher: CipherDependency
) -> TargetOut:
    """Pin the host key the last test saw, when its fingerprint is the one the user checked; then test again."""
    target = find_target(session, target_id)
    if not target.pending_host_key:
        raise HTTPException(status.HTTP_409_CONFLICT, "No host key waits for confirmation; run Test first.")
    if fingerprint(target.pending_host_key) != payload.fingerprint.strip():
        raise HTTPException(status.HTTP_409_CONFLICT, "That is not the fingerprint YAAP saw; run Test again.")
    target.host_key, target.pending_host_key = target.pending_host_key, None
    session.commit()
    return await run_test(session, cipher, target)


@router.get("/targets/{target_id}/setup-script", response_class=PlainTextResponse)
def setup_script(
    target_id: int, session: SessionDependency, rights: Annotated[Literal["standard", "administrator"], Query()] = STANDARD
) -> str:
    """The PowerShell script that lets YAAP work on this Windows computer, for the user to read and run."""
    target = find_target(session, target_id)
    public_key = public_key_of(session, target)
    if not public_key:
        raise HTTPException(status.HTTP_409_CONFLICT, "This target has no key; remove it and add it again.")
    return windows_setup_script(public_key, ADMINISTRATOR if rights == ADMINISTRATOR else STANDARD, list(target.folders or []), target.user)
