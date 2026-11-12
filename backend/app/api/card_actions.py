"""A card's stages from the chat: *Make a card* on a message, and the card's actions (*Plan*, *Approve and build*,
*Continue*, *Stop*, *Review*, *Mark done*).

Thin: each route calls the card's route (app/board/route.py) and answers with the card as it is afterwards. Board
errors answer as the board's routes do; a build that cannot start or go on answers 409 with the build's `code`.
"""

from typing import Annotated, Literal

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.board import BoardDependency, board_http_error
from app.api.board_schemas import CardDetailOut, card_detail_out
from app.board.card_command import card_from_message
from app.board.errors import BoardError
from app.board.route import CardRoute
from app.board.views import CardDetail
from app.plans.service import BuildRefusal, BuildRefused, PlanNotFound

router = APIRouter(tags=["board"])

CardAction = Literal["plan", "build", "continue", "stop", "review", "mark_done"]


class CardActionOut(BaseModel):
    card: CardDetailOut
    # The conversation *Plan* started, where the plan is made.
    conversation_id: int | None = None
    # The review *Review* started.
    review_id: int | None = None


class SendBackIn(BaseModel):
    # The index of the review's finding, or the number of an acceptance criterion that failed or was judged not met.
    finding: int | None = Field(default=None, ge=0)
    criterion: int | None = Field(default=None, ge=1)


class BuildRefusalOut(BaseModel):
    code: BuildRefusal
    message: str


def card_route(request: Request) -> CardRoute:
    route: CardRoute = request.app.state.card_route
    return route


CardRouteDependency = Annotated[CardRoute, Depends(card_route)]


async def run_action(route: CardRoute, project_id: int, card_id: int, action: CardAction) -> CardActionOut:
    conversation_id: int | None = None
    review_id: int | None = None
    detail: CardDetail | None
    if action == "plan":
        conversation_id = await anyio.to_thread.run_sync(route.plan, project_id, card_id)
        detail = None
    elif action == "build":
        detail = await route.build(project_id, card_id)
    elif action == "continue":
        detail = await route.continue_build(project_id, card_id)
    elif action == "stop":
        detail = await route.stop(project_id, card_id)
    elif action == "review":
        review_id = await anyio.to_thread.run_sync(route.review, project_id, card_id)
        detail = None
    else:
        detail = await anyio.to_thread.run_sync(route.mark_done, project_id, card_id)
    if detail is None:
        detail = await anyio.to_thread.run_sync(route.board.get_card, project_id, card_id)
    return CardActionOut(card=card_detail_out(detail), conversation_id=conversation_id, review_id=review_id)


@router.post(
    "/projects/{project_id}/board/cards/{card_id}/actions/{action}",
    response_model=CardActionOut,
    responses={409: {"model": BuildRefusalOut}},
)
async def card_action(project_id: int, card_id: int, action: CardAction, route: CardRouteDependency) -> CardActionOut:
    """Run one of the card's stage actions; the card's next step says which one is due."""
    try:
        return await run_action(route, project_id, card_id, action)
    except BoardError as error:
        raise board_http_error(error) from error
    except BuildRefused as error:
        raise HTTPException(status.HTTP_409_CONFLICT, {"code": error.code, "message": str(error)}) from error
    except PlanNotFound as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error


@router.post("/projects/{project_id}/board/cards/{card_id}/send-back", response_model=CardDetailOut)
async def send_back_card(
    project_id: int, card_id: int, payload: SendBackIn, route: CardRouteDependency
) -> CardDetailOut:
    """Send the card back to Design with one finding or unmet criterion of its review in its description."""
    try:
        send_back = route.send_back
        detail = await anyio.to_thread.run_sync(send_back, project_id, card_id, payload.finding, payload.criterion)
    except BoardError as error:
        raise board_http_error(error) from error
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
