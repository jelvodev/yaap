"""A card's stages from the board and the chat: *Make a card* on a message, the card's actions (*Specify*, *Plan*,
*Approve and build*, *Continue*, *Stop*, *Review*, *Mark done*) and *Send back*.

Thin: each route calls the board's actions (app/board/actions.py) and answers with the card as it is afterwards. A
refused action answers 409 with a `code`: the board error's, the flow's rule for a refused move (`wrong_column`,
`card_broken`, `build_running`, `waits_for_open` with the card numbers in `waits_for`), or the build's.
"""

from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.api.board import BoardDependency, board_http_error
from app.api.board_schemas import CardDetailOut, card_detail_out
from app.board.actions import ActionResult, ActionService, CardAction, WaitsForOpen
from app.board.card_command import card_from_message
from app.board.errors import BoardError, MoveRefused
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
