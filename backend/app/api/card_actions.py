"""A card's stages from the board and the chat: *Make a card* on a message, the card's actions (*Specify*, *Plan*,
*Approve and build*, *Continue*, *Stop*, *Review*, *Mark done*) and *Send back*.

Thin: each route calls the board's actions (app/board/actions.py) and answers with the card as it is afterwards. A
refused action answers 409 with a `code`: the board error's, the flow's rule for a refused move (`wrong_column`,
`card_broken`, `build_running`, `waits_for_open` with the card numbers in `waits_for`), or the build's.
"""

from datetime import datetime
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.api.board import BoardDependency, board_http_error
from app.api.board_schemas import CardDetailOut, card_detail_out
from app.board.actions import ActionResult, ActionService, CardAction, WaitsForOpen
from app.board.card_command import card_from_message
from app.board.errors import BoardError, MoveRefused
from app.board.version_merge import MergeResult, MergeState, MergeStatus
from app.plans.service import BuildRefusal, BuildRefused, PlanNotFound

router = APIRouter(tags=["board"])


class CardActionOut(BaseModel):
    card: CardDetailOut
    # The conversation *Specify* or *Plan* started, where the specification or the plan is made.
    conversation_id: int | None = None
    # The review *Review* started.
    review_id: int | None = None


class SendBackIn(BaseModel):
    # The index of the review's finding, or the number of an acceptance criterion that failed or was judged not met;
    # neither for a card whose build failed or was interrupted.
    finding: int | None = Field(default=None, ge=0)
    criterion: int | None = Field(default=None, ge=1)


class MergeIn(BaseModel):
    # The version's check commands as the user saw them next to the button; a command that is not among them is not run.
    commands: list[Annotated[str, Field(max_length=500)]] = Field(default_factory=list, max_length=50)


class MergeResultOut(BaseModel):
    status: MergeStatus
    output: str
    commit: str | None
    finished_at: datetime | None


class MergeStateOut(BaseModel):
    version: str | None
    branch: str | None
    merged: bool
    running: bool
    offered: bool
    refusal: str | None
    version_commands: list[str]
    card_commands: list[str]
    last: MergeResultOut | None


def merge_result_out(result: MergeResult) -> MergeResultOut:
    return MergeResultOut(
        status=result.status, output=result.output, commit=result.commit, finished_at=result.finished_at
    )


def merge_state_out(state: MergeState) -> MergeStateOut:
    return MergeStateOut(
        version=state.version,
        branch=state.branch,
        merged=state.merged,
        running=state.running,
        offered=state.offered,
        refusal=state.refusal,
        version_commands=list(state.version_commands),
        card_commands=list(state.card_commands),
        last=merge_result_out(state.last) if state.last is not None else None,
    )


class BuildRefusalOut(BaseModel):
    code: BuildRefusal
    message: str


def board_actions(request: Request) -> ActionService:
    actions: ActionService = request.app.state.board_actions
    return actions


ActionsDependency = Annotated[ActionService, Depends(board_actions)]


def action_http_error(error: BoardError) -> HTTPException:
    """A board error as the actions answer it: a refused move carries the flow's rule as its code."""
    if not isinstance(error, MoveRefused):
        return board_http_error(error)
    code = "waits_for_open" if error.refusal_code == "waits_for" else error.refusal_code
    detail: dict[str, object] = {"code": code, "message": str(error), "reason": error.refusal_code}
    if isinstance(error, WaitsForOpen):
        detail["waits_for"] = list(error.waits_for)
    return HTTPException(status.HTTP_409_CONFLICT, detail)


def action_out(result: ActionResult) -> CardActionOut:
    return CardActionOut(
        card=card_detail_out(result.card), conversation_id=result.conversation_id, review_id=result.review_id
    )


@router.post(
    "/projects/{project_id}/board/cards/{card_id}/actions/{action}",
    response_model=CardActionOut,
    responses={202: {"model": CardActionOut}, 409: {"model": BuildRefusalOut}},
)
async def card_action(
    project_id: int, card_id: int, action: CardAction, actions: ActionsDependency, response: Response
) -> CardActionOut:
    """Run one of the card's stage actions; the card's next step says which one is due. Specify and Plan answer 202:
    they start a conversation, whose id is in the answer."""
    try:
        result = await actions.run(project_id, card_id, action)
    except BoardError as error:
        raise action_http_error(error) from error
    except BuildRefused as error:
        raise HTTPException(status.HTTP_409_CONFLICT, {"code": error.code, "message": str(error)}) from error
    except PlanNotFound as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    if action in ("specify", "plan"):
        response.status_code = status.HTTP_202_ACCEPTED
    return action_out(result)


@router.post("/projects/{project_id}/board/cards/{card_id}/send-back", response_model=CardDetailOut)
async def send_back_card(
    project_id: int, card_id: int, payload: SendBackIn, actions: ActionsDependency
) -> CardDetailOut:
    """Send the card back to Design: from Review with one finding or unmet criterion of its review in its description,
    from Build when its build failed or was interrupted."""
    try:
        send_back = actions.send_back
        detail = await anyio.to_thread.run_sync(send_back, project_id, card_id, payload.finding, payload.criterion)
    except BoardError as error:
        raise action_http_error(error) from error
    return card_detail_out(detail)


@router.get("/projects/{project_id}/board/cards/{card_id}/merge", response_model=MergeStateOut)
async def merge_state(project_id: int, card_id: int, actions: ActionsDependency) -> MergeStateOut:
    """What *Merge into the version* offers for the card: its version branch, the checks and the last result."""
    try:
        return merge_state_out(await anyio.to_thread.run_sync(actions.merges.state, project_id, card_id))
    except BoardError as error:
        raise action_http_error(error) from error
    except PlanNotFound as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error


@router.post(
    "/projects/{project_id}/board/cards/{card_id}/merge",
    response_model=MergeStateOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def merge_card_into_version(
    project_id: int, card_id: int, payload: MergeIn, actions: ActionsDependency
) -> MergeStateOut:
    """Merge the card's branch into its version's branch in the background. `commands` are the version's checks the
    user saw next to the button."""
    try:
        return merge_state_out(await actions.merges.merge(project_id, card_id, tuple(payload.commands)))
    except BoardError as error:
        raise action_http_error(error) from error
    except PlanNotFound as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error


@router.post(
    "/conversations/{conversation_id}/messages/{message_id}/card",
    response_model=CardDetailOut,
    status_code=status.HTTP_201_CREATED,
)
def make_card(conversation_id: int, message_id: int, request: Request, board: BoardDependency) -> CardDetailOut:
    """*Make a card*: the message's text becomes a card in Design, linked to the conversation."""
    try:
        return card_detail_out(card_from_message(board, request.app.state.session_factory, conversation_id, message_id))
    except BoardError as error:
        raise board_http_error(error) from error
