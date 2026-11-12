"""Cards into a board's Inbox from outside: GitHub issues imported by number or label, and *Make a card* on a review's
finding.

Thin: each route calls the intake (app/board/intake.py, app/board/issues.py) and answers board errors as the board's
routes do. Issue text is stored as data and never rendered here.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.board import board_http_error
from app.api.board_intake_schemas import FindingCard, IssueImport, IssueImportOut, import_out
from app.api.board_schemas import CardDetailOut, card_detail_out
from app.api.dependencies import CipherDependency, SessionDependency
from app.board.errors import BoardError, BoardErrorCode
from app.board.intake import CardIntake
from app.board.issues import IssueImporter, IssueSelection
from app.credentials.crypto import CredentialDecryptionError
from app.credentials.store import read_credential_value
from app.storage.models import Project

router = APIRouter(prefix="/projects/{project_id}/board", tags=["board"])

# The intake's own codes that are not a 409; the rest answer as the board's routes do.
STATUS_BY_CODE: dict[BoardErrorCode, int] = {
    "finding_not_found": status.HTTP_404_NOT_FOUND,
    "github_failed": status.HTTP_502_BAD_GATEWAY,
}


def intake_http_error(error: BoardError) -> HTTPException:
    found = STATUS_BY_CODE.get(error.code)
    if found is None:
        return board_http_error(error)
    return HTTPException(found, {"code": error.code, "message": str(error)})


def card_intake(request: Request) -> CardIntake:
    return CardIntake(request.app.state.board)


IntakeDependency = Annotated[CardIntake, Depends(card_intake)]


@router.post("/import-issues", response_model=IssueImportOut)
def import_issues(
    project_id: int,
    payload: IssueImport,
    intake: IntakeDependency,
    session: SessionDependency,
    cipher: CipherDependency,
) -> IssueImportOut:
    """Import GitHub issues of the project's repository into Inbox, one way; issues imported before are skipped."""

    def token(project: Project) -> str | None:
        return read_credential_value(
            session, cipher, project.environment_id, "github_token", project.id, f"Import issues: {project.name}"
        )

    selection = IssueSelection(numbers=tuple(payload.numbers), label=payload.label)
    try:
        result = IssueImporter(intake).import_issues(project_id, selection, token)
    except CredentialDecryptionError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, {"code": "github_failed", "message": str(error)}) from error
    except BoardError as error:
        raise intake_http_error(error) from error
    return import_out(result)


@router.post("/cards/from-finding", response_model=CardDetailOut, status_code=status.HTTP_201_CREATED)
def card_from_finding(project_id: int, payload: FindingCard, intake: IntakeDependency) -> CardDetailOut:
    """*Make a card* on a review's finding that the change did not fix: a card in Inbox with origin `review`."""
    try:
        return card_detail_out(intake.add_from_finding(project_id, payload.review_id, payload.finding))
    except BoardError as error:
        raise intake_http_error(error) from error

