"""A project's board: the board itself, its cards, versions and push.

Thin: each route calls the board service and turns its errors into answers with `{"code", "message"}` (plus the
refusal's `reason` code for a refused move and the current card for `card_changed`). The board's logic is in
`app.board`; card text is returned as stored, never rendered here.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from app.api.board_schemas import (
    BoardOut,
    BoardPush,
    BoardStart,
    BoardVersionOut,
    CardAnswer,
    CardCreate,
    CardDetailOut,
    CardMove,
    CardPatch,
    PushedOut,
    VersionPut,
    board_out,
    card_detail_out,
    version_out,
)
from app.api.dependencies import CipherDependency, SessionDependency
from app.board.card import Origin
from app.board.errors import BoardError, BoardErrorCode, CardChanged, MoveRefused
from app.board.operations import NewCard
from app.board.service import BoardService
from app.board.views import CardDetail
from app.credentials.crypto import CredentialDecryptionError
from app.credentials.store import read_credential_value
from app.storage.models import Project

router = APIRouter(prefix="/projects/{project_id}/board", tags=["board"])

STATUS_BY_CODE: dict[BoardErrorCode, int] = {
    "project_not_found": status.HTTP_404_NOT_FOUND,
    "card_not_found": status.HTTP_404_NOT_FOUND,
    "invalid_card": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "invalid_version": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "path_refused": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "board_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
    "push_failed": status.HTTP_502_BAD_GATEWAY,
}


def board_service(request: Request) -> BoardService:
    return request.app.state.board


BoardDependency = Annotated[BoardService, Depends(board_service)]


def board_http_error(error: BoardError) -> HTTPException:
    """The answer for a board error: its status, code and safe sentence; 409 for every conflict."""
    detail: dict[str, object] = {"code": error.code, "message": str(error)}
    if isinstance(error, MoveRefused):
        detail["reason"] = error.refusal_code
    if isinstance(error, CardChanged) and isinstance(error.current, CardDetail):
        detail["current"] = card_detail_out(error.current).model_dump(mode="json")
    return HTTPException(STATUS_BY_CODE.get(error.code, status.HTTP_409_CONFLICT), detail)


@router.get("", response_model=BoardOut)
def read_board(
    project_id: int,
    board: BoardDependency,
    since: Annotated[str | None, Query(max_length=64)] = None,
) -> BoardOut:
    """The board; with `since` equal to its head, only `unchanged` (cheap to poll)."""
    try:
        return board_out(board.overview(project_id, since))
    except BoardError as error:
        raise board_http_error(error) from error


@router.post("", response_model=BoardOut, status_code=status.HTTP_201_CREATED)
def start_board(project_id: int, payload: BoardStart, board: BoardDependency) -> BoardOut:
    try:
        return board_out(board.enable(project_id, payload.storage, payload.create_repository))
    except BoardError as error:
        raise board_http_error(error) from error


@router.post("/cards", response_model=CardDetailOut, status_code=status.HTTP_201_CREATED)
def create_card(project_id: int, payload: CardCreate, board: BoardDependency) -> CardDetailOut:
    request = NewCard(
        title=payload.title,
        kind=payload.kind,
        column=payload.column,
        version=payload.version,
        description=payload.description,
        origin=Origin(type="user"),
    )
    try:
        return card_detail_out(board.create_card(project_id, request))
    except BoardError as error:
        raise board_http_error(error) from error


@router.get("/cards/{card_id}", response_model=CardDetailOut)
def read_card(project_id: int, card_id: int, board: BoardDependency) -> CardDetailOut:
    try:
        return card_detail_out(board.get_card(project_id, card_id))
    except BoardError as error:
        raise board_http_error(error) from error


@router.patch("/cards/{card_id}", response_model=CardDetailOut)
def update_card(project_id: int, card_id: int, payload: CardPatch, board: BoardDependency) -> CardDetailOut:
    """Change a card's text and plain fields; its column changes only by a move."""
    patch = payload.model_dump(exclude_unset=True, exclude={"expected_updated"})
    try:
        return card_detail_out(board.update_card(project_id, card_id, patch, payload.expected_updated))
    except BoardError as error:
        raise board_http_error(error) from error


@router.post("/cards/{card_id}/move", response_model=CardDetailOut)
def move_card(project_id: int, card_id: int, payload: CardMove, board: BoardDependency) -> CardDetailOut:
    try:
        return card_detail_out(board.move_card(project_id, card_id, payload.column, "user", payload.expected_updated))
    except BoardError as error:
        raise board_http_error(error) from error


@router.post("/cards/{card_id}/answer", response_model=CardDetailOut)
def answer_question(project_id: int, card_id: int, payload: CardAnswer, board: BoardDependency) -> CardDetailOut:
    try:
        return card_detail_out(board.answer_question(project_id, card_id, payload.index, payload.text))
    except BoardError as error:
        raise board_http_error(error) from error


@router.delete("/cards/{card_id}", status_code=status.HTTP_204_NO_CONTENT)
def drop_card(project_id: int, card_id: int, board: BoardDependency) -> Response:
    try:
        board.drop_card(project_id, card_id, "user")
    except BoardError as error:
        raise board_http_error(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/versions/{name}", response_model=BoardVersionOut)
def put_version(project_id: int, name: str, payload: VersionPut, board: BoardDependency) -> BoardVersionOut:
    """Make a version or change it; only the fields sent change."""
    try:
        return version_out(board.put_version(project_id, name, payload.model_dump(exclude_unset=True)))
    except BoardError as error:
        raise board_http_error(error) from error


@router.post("/push", response_model=PushedOut)
def push_board(
    project_id: int,
    payload: BoardPush,
    board: BoardDependency,
    session: SessionDependency,
    cipher: CipherDependency,
) -> PushedOut:
    """Push the board's branch alone to the project's remote, after the user's confirmation for a public one."""

    def token(project: Project) -> str | None:
        return read_credential_value(
            session, cipher, project.environment_id, "github_token", project.id, f"Push the board: {project.name}"
        )

    try:
        return PushedOut(pushed_commit=board.push(project_id, payload.acknowledge_public, token))
    except CredentialDecryptionError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, {"code": "push_failed", "message": str(error)}) from error
    except BoardError as error:
        raise board_http_error(error) from error
