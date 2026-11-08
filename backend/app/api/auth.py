"""Signing in, the first-run password, the health check and API tokens."""

import time
from collections import deque
from datetime import datetime
from typing import Literal

import anyio
from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.dependencies import CipherDependency, SessionDependency
from app.auth.account import (
    AccountError,
    NewSession,
    check_password,
    end_other_sessions,
    end_session,
    password_is_set,
    set_password,
    start_session,
)
from app.auth.gate import Identity, csrf_cookie_name, identify, request_identity, session_cookie_name
from app.auth.tokens import API_TOKEN, PURPOSES, create_token, revoke_token, token_entries, token_of
from app.storage.models import Environment, Project, VaultEntry
from app.vault.store import VaultError

router = APIRouter(tags=["auth"])

# Sign-in attempts: after five wrong passwords within a minute, the next attempts wait.
MAX_FAILURES = 5
FAILURE_WINDOW = 60.0
failures: deque[float] = deque()
# 400 days: the longest a browser keeps a cookie.
COOKIE_MAX_AGE = 400 * 24 * 3600


class HealthOut(BaseModel):
    status: Literal["ok"]


class AuthStateOut(BaseModel):
    # Whether the account has a password; without one the browser shows the first-run screen.
    password_set: bool
    signed_in: bool
    method: Literal["session", "token"] | None = None


class PasswordIn(BaseModel):
    password: str = Field(max_length=1024)


class PasswordChangeIn(BaseModel):
    current_password: str = Field(max_length=1024)
    new_password: str = Field(max_length=1024)


class ApiTokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    purpose: Literal["api", "mcp", "a2a"]
    # mcp and a2a: the environment (and optionally the project) the token is confined to.
    environment_id: int | None = None
    project_id: int | None = None
    description: str = Field(default="", max_length=300)
    expires_at: datetime | None = None


class ApiTokenOut(BaseModel):
    id: int
    name: str
    purpose: Literal["api", "mcp", "a2a"]
    purpose_label: str
    environment_id: int | None
    project_id: int | None
    # Where the token works, for example "Work › Website", or "Everything" for an api token.
    scope_name: str
    description: str
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None


class ApiTokenCreatedOut(ApiTokenOut):
    # Shown once: YAAP keeps only an encrypted copy and a hash.
    token: str


@router.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    """Open without signing in: whether YAAP answers (the setup script waits for it)."""
    return HealthOut(status="ok")


def is_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https"


def set_session_cookies(request: Request, response: Response, new: NewSession) -> None:
    # The server ends a session after 30 idle days; the cookie itself may live as long as browsers allow.
    max_age = COOKIE_MAX_AGE
    secure = is_https(request)
    response.set_cookie(
        session_cookie_name(request), new.token, max_age=max_age, path="/", httponly=True, samesite="strict", secure=secure
    )
    # The page reads this one and sends it back in X-CSRF-Token; another site can neither read nor send it.
    response.set_cookie(
        csrf_cookie_name(request), new.csrf, max_age=max_age, path="/", httponly=False, samesite="strict", secure=secure
    )


def clear_session_cookies(request: Request, response: Response) -> None:
    response.delete_cookie(session_cookie_name(request), path="/", samesite="strict")
    response.delete_cookie(csrf_cookie_name(request), path="/", samesite="strict")


def session_identity(request: Request) -> Identity:
    """Managing the account and its tokens needs a signed-in browser; an API token cannot make more tokens."""
    identity = request_identity(request)
    if identity is None or identity.method != "session":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only a signed-in browser can do this, not an API token.")
    return identity


@router.get("/auth/state", response_model=AuthStateOut)
def auth_state(request: Request, session: SessionDependency) -> AuthStateOut:
    identity = identify(request)
    return AuthStateOut(
        password_set=password_is_set(session),
        signed_in=identity is not None,
        method=identity.method if identity is not None else None,  # type: ignore[arg-type]
    )


def sign_in_response(request: Request, response: Response, session: Session) -> AuthStateOut:
    new = start_session(session, request.headers.get("user-agent", ""))
    session.commit()
    set_session_cookies(request, response, new)
    return AuthStateOut(password_set=True, signed_in=True, method="session")


@router.post("/auth/setup", response_model=AuthStateOut)
def set_first_password(payload: PasswordIn, request: Request, response: Response, session: SessionDependency) -> AuthStateOut:
    """The first-run screen: set the password of an installation that has none yet, and sign in."""
    if password_is_set(session):
        raise HTTPException(status.HTTP_409_CONFLICT, "YAAP already has a password; sign in instead.")
    try:
        set_password(session, request.app.state.password_hasher, payload.password)
    except AccountError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    return sign_in_response(request, response, session)


def too_many_failures() -> bool:
    moment = time.monotonic()
    while failures and moment - failures[0] > FAILURE_WINDOW:
        failures.popleft()
    return len(failures) >= MAX_FAILURES


@router.post("/auth/sign-in", response_model=AuthStateOut)
async def sign_in(payload: PasswordIn, request: Request, response: Response) -> AuthStateOut:
    if too_many_failures():
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many wrong passwords; wait a minute and try again.")

    def attempt() -> AuthStateOut | None:
        with request.app.state.session_factory() as session:
            if not password_is_set(session):
                raise HTTPException(status.HTTP_409_CONFLICT, "YAAP has no password yet; set one first.")
            if not check_password(session, request.app.state.password_hasher, payload.password):
                return None
            return sign_in_response(request, response, session)

    # Hashing takes a moment and much memory on purpose; it runs beside the event loop.
    state = await anyio.to_thread.run_sync(attempt)
    if state is None:
        failures.append(time.monotonic())
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That password is not right.")
    return state


@router.post("/auth/sign-out", status_code=status.HTTP_204_NO_CONTENT)
def sign_out(request: Request, session: SessionDependency) -> Response:
    identity = request_identity(request)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    if identity is not None and identity.session_id is not None:
        end_session(session, identity.session_id)
        session.commit()
    clear_session_cookies(request, response)
    return response


@router.put("/auth/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(payload: PasswordChangeIn, request: Request, session: SessionDependency) -> Response:
    """Change the password; every other signed-in browser is signed out."""
    identity = session_identity(request)
    hasher = request.app.state.password_hasher
    if not check_password(session, hasher, payload.current_password):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The current password is not right.")
    try:
        set_password(session, hasher, payload.new_password)
    except AccountError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    end_other_sessions(session, identity.session_id)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/auth/sign-out-everywhere", status_code=status.HTTP_204_NO_CONTENT)
def sign_out_everywhere(request: Request, session: SessionDependency) -> Response:
    """End every browser session but this one."""
    identity = session_identity(request)
    end_other_sessions(session, identity.session_id)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def scope_name(session: Session, environment_id: int | None, project_id: int | None) -> str:
    if environment_id is None:
        return "Everything"
    environment = session.get(Environment, environment_id)
    name = environment.name if environment is not None else f"environment {environment_id} (removed)"
    if project_id is not None:
        project = session.get(Project, project_id)
        name += f" › {project.name if project is not None else f'project {project_id} (removed)'}"
    return name


def token_out(session: Session, entry: VaultEntry) -> ApiTokenOut:
    token = token_of(entry)
    return ApiTokenOut(
        id=entry.id,
        name=entry.name,
        purpose=token.purpose,
        purpose_label=PURPOSES.get(token.purpose, token.purpose),
        environment_id=token.environment_id,
        project_id=token.project_id,
        scope_name=scope_name(session, token.environment_id, token.project_id),
        description=entry.description,
        created_at=entry.created_at,
        last_used_at=entry.last_used_at,
        expires_at=entry.expires_at,
    )


@router.get("/auth/tokens", response_model=list[ApiTokenOut])
def list_tokens(request: Request, session: SessionDependency) -> list[ApiTokenOut]:
    session_identity(request)
    return [token_out(session, entry) for entry in token_entries(session)]


@router.post("/auth/tokens", response_model=ApiTokenCreatedOut, status_code=status.HTTP_201_CREATED)
def make_token(payload: ApiTokenIn, request: Request, session: SessionDependency, cipher: CipherDependency) -> ApiTokenCreatedOut:
    session_identity(request)
    try:
        entry, token = create_token(
            session,
            cipher,
            name=payload.name,
            purpose=payload.purpose,
            environment_id=payload.environment_id,
            project_id=payload.project_id,
            description=payload.description,
            expires_at=payload.expires_at,
        )
    except VaultError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    session.commit()
    return ApiTokenCreatedOut(**token_out(session, entry).model_dump(), token=token)


@router.delete("/auth/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_token(token_id: int, request: Request, session: SessionDependency) -> Response:
    """Revoke a token: it stops working at once."""
    session_identity(request)
    if not revoke_token(session, token_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Token not found.")
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["API_TOKEN", "router"]
