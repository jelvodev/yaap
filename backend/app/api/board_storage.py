"""A board kept in a separate repository: pulling it and checking a repository before the board starts there.

Thin: each route calls the board's storage (`app.board.storage.BoardStorages`) and turns its errors into the board's
answers (`app.api.board.board_http_error`). Pushing is the board's own push route, for both places a board is kept.
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.board import BoardDependency, board_http_error
from app.board.errors import BoardError

router = APIRouter(prefix="/projects/{project_id}/board", tags=["board"])


class PulledOut(BaseModel):
    # The board's commit after the pull; None while its repository has no board yet.
    head: str | None


class RemoteCheckIn(BaseModel):
    repository_url: str = Field(min_length=1, max_length=500)


class RemoteCheckOut(BaseModel):
    ok: bool
    # Whether the repository is private; None when YAAP cannot tell (not on GitHub, or no token).
    private: bool | None
    # Why the repository cannot be used, when `ok` is false.
    message: str | None


@router.post("/pull", response_model=PulledOut)
def pull_board(project_id: int, board: BoardDependency) -> PulledOut:
    """Fetch the board's repository and fast-forward the board; nothing changes when both sides moved."""
    try:
        return PulledOut(head=board.storage.pull(board.project(project_id)))
    except BoardError as error:
        raise board_http_error(error) from error


@router.post("/check-remote", response_model=RemoteCheckOut)
def check_remote(project_id: int, payload: RemoteCheckIn, board: BoardDependency) -> RemoteCheckOut:
    """Whether YAAP can reach a repository for the board with the project's GitHub token, and whether it is private."""
    try:
        project = board.project(project_id)
    except BoardError as error:
        raise board_http_error(error) from error
    found = board.storage.repository.check(project, payload.repository_url)
    return RemoteCheckOut(ok=found.ok, private=found.private, message=found.message)
