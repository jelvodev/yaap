"""Signing in to Claude Code and Codex from the browser."""

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.providers.cli_sign_in import (
    CliSignIns,
    CodeRefused,
    NoSignIn,
    SignInState,
    SignInStatus,
    UnknownCli,
)

router = APIRouter(tags=["sign-in"])

class SignInOut(BaseModel):
    state: SignInState
    # The page at the vendor where the user signs in; always https on the vendor's own site.
    url: str | None
    # The one-time code to enter at that page (Codex).
    user_code: str | None
    # Whether the user pastes a code back (Claude Code) and has already done so.
    code_sent: bool
    message: str | None


class SignInCodeIn(BaseModel):
    code: str = Field(min_length=1, max_length=2000)


def sign_ins_of(request: Request) -> CliSignIns:
    return request.app.state.cli_sign_ins


def out(result: SignInStatus) -> SignInOut:
    return SignInOut(
        state=result.state,
        url=result.url,
        user_code=result.user_code,
        code_sent=result.code_sent,
        message=result.message,
    )


def not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "This connection cannot be signed in from the browser.")


def add_routes(cli: str) -> None:
    """The four routes of one CLI under its own path, so none can be mistaken for another route."""

    async def read_sign_in(request: Request) -> SignInOut:
        """Where the sign-in stands."""
        try:
            return out(sign_ins_of(request).status(cli))
        except UnknownCli:
            raise not_found() from None

    async def start_sign_in(request: Request) -> SignInOut:
        """Start signing in; a sign-in that is already running is cancelled first."""
        try:
            return out(await sign_ins_of(request).start(cli))
        except UnknownCli:
            raise not_found() from None

    async def send_sign_in_code(body: SignInCodeIn, request: Request) -> SignInOut:
        """Give the CLI the code that the sign-in page showed (Claude Code)."""
        try:
            return out(await sign_ins_of(request).send_code(cli, body.code))
        except UnknownCli:
            raise not_found() from None
        except NoSignIn as error:
            raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from None
        except CodeRefused as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from None

    async def cancel_sign_in(request: Request) -> SignInOut:
        """Stop the sign-in that is running."""
        try:
            return out(await sign_ins_of(request).cancel(cli))
        except UnknownCli:
            raise not_found() from None

    name = cli.replace("-", "_")
    path = f"/{cli}/sign-in"
    router.add_api_route(path, read_sign_in, methods=["GET"], response_model=SignInOut, name=f"read_{name}_sign_in")
    router.add_api_route(path, start_sign_in, methods=["POST"], response_model=SignInOut, name=f"start_{name}_sign_in")
    router.add_api_route(
        f"{path}/code", send_sign_in_code, methods=["POST"], response_model=SignInOut, name=f"send_{name}_sign_in_code"
    )
    router.add_api_route(path, cancel_sign_in, methods=["DELETE"], response_model=SignInOut, name=f"cancel_{name}_sign_in")


for cli_name in ("claude-code", "codex"):
    add_routes(cli_name)
