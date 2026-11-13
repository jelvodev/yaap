"""Credential management per environment. Values are write-only: no endpoint returns them."""

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api.dependencies import (
    CipherDependency,
    CredentialTypesDependency,
    EnvironmentDependency,
    SessionDependency,
)
from app.api.schemas import CredentialSave, CredentialStatusOut, CredentialTestOut
from app.credentials.crypto import CredentialDecryptionError
from app.credentials.store import find_credential, read_credential_value, record_test_result, save_credential
from app.credentials.types import CredentialType
from app.vault.store import remove_entry

router = APIRouter(prefix="/environments/{environment_id}/credentials", tags=["credentials"])


def require_credential_type(kind: str, credential_types: dict[str, CredentialType]) -> CredentialType:
    credential_type = credential_types.get(kind)
    if credential_type is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown credential type.")
    return credential_type


def build_status(credential_type: CredentialType, environment_id: int, session: Session) -> CredentialStatusOut:
    credential = find_credential(session, environment_id, credential_type.kind)
    return CredentialStatusOut(
        kind=credential_type.kind,
        label=credential_type.label,
        description=credential_type.description,
        configured=credential is not None,
        last_four=credential.last_four if credential else None,
        updated_at=credential.updated_at if credential else None,
        last_tested_at=credential.last_tested_at if credential else None,
        last_test_result=credential.last_test_result if credential else None,
    )


@router.get("", response_model=list[CredentialStatusOut])
def list_credentials(
    environment: EnvironmentDependency, session: SessionDependency, credential_types: CredentialTypesDependency
) -> list[CredentialStatusOut]:
    return [build_status(credential_type, environment.id, session) for credential_type in credential_types.values()]


@router.put("/{kind}", response_model=CredentialStatusOut)
async def store_credential(
    kind: str,
    payload: CredentialSave,
    environment: EnvironmentDependency,
    session: SessionDependency,
    cipher: CipherDependency,
    credential_types: CredentialTypesDependency,
) -> CredentialStatusOut:
    credential_type = require_credential_type(kind, credential_types)
    value = payload.value.strip()
    result = await credential_type.test(value)
    if not result.ok:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"The key was not saved: {result.message}")
    save_credential(session, cipher, environment.id, kind, value, result)
    return build_status(credential_type, environment.id, session)


@router.post("/{kind}/test", response_model=CredentialTestOut)
async def test_stored_credential(
    kind: str,
    environment: EnvironmentDependency,
    session: SessionDependency,
    cipher: CipherDependency,
    credential_types: CredentialTypesDependency,
) -> CredentialTestOut:
    credential_type = require_credential_type(kind, credential_types)
    credential = find_credential(session, environment.id, kind)
    if credential is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No key is stored for this environment.")
    try:
        value = read_credential_value(session, cipher, environment.id, kind)
    except CredentialDecryptionError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    assert value is not None
    result = await credential_type.test(value)
    record_test_result(session, credential, result)
    return CredentialTestOut(ok=result.ok, message=result.message)


@router.delete("/{kind}", status_code=status.HTTP_204_NO_CONTENT)
def delete_credential(
    kind: str,
    environment: EnvironmentDependency,
    session: SessionDependency,
    credential_types: CredentialTypesDependency,
) -> Response:
    require_credential_type(kind, credential_types)
    credential = find_credential(session, environment.id, kind)
    if credential is not None:
        remove_entry(session, credential)
        session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
