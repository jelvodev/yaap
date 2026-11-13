"""A card from the chat: `/card <title or request>` in a project conversation, and *Make a card* on a message.

The card goes straight into Design (a request the user typed is already accepted), with the request as its
description and the conversation linked to it with role `chat`. A project whose board was never started gets its
board with the first card; with the board switched off the command is refused with the switch's sentence.
"""

import re
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.board.card import MAX_TITLE, Origin
from app.board.errors import BoardError, BoardNotStarted, InvalidCard
from app.board.operations import NewCard
from app.board.service import BoardService
from app.board.stage_text import escape_headings
from app.board.views import CardDetail
from app.core.reply_errors import CommandRefused
from app.core.search_index import message_text
from app.storage.models import Conversation, Message

CARD_COMMAND = re.compile(r"^/card(?:\s+(.*))?$", re.DOTALL)
MAX_REQUEST = 20_000


def card_title(request: str) -> str:
    """The card's title: the request's first line, cut at a word within the title's limit."""
    first = next((line.strip() for line in request.splitlines() if line.strip()), "")
    first = first.lstrip("#").strip() or "Card from the chat"
    if len(first) <= MAX_TITLE:
        return first
    cut = first[: MAX_TITLE - 1]
    space = cut.rfind(" ")
    return (cut[:space] if space > MAX_TITLE // 2 else cut).rstrip() + "…"


def make_card(board: BoardService, project_id: int, conversation_id: int, request: str, origin_ref: str) -> CardDetail:
    """A card in Design from a request in the chat, linked to the conversation. Raises a BoardError."""
    if len(request) > MAX_REQUEST:
        raise InvalidCard(f"The request is over {MAX_REQUEST} characters; make the card from a shorter text.")
    new = NewCard(
        title=card_title(request),
        column="idea",
        description=escape_headings(request),
        origin=Origin(type="chat", ref=origin_ref),
    )
    try:
        created = board.create_card(project_id, new)
    except BoardNotStarted:
        board.enable(project_id)
        created = board.create_card(project_id, new)
    card_id = created.view.id
    board.move_card(project_id, card_id, "design", actor="user")
    board.link_conversation(project_id, card_id, conversation_id, "chat")
    return board.get_card(project_id, card_id)


def card_reply_text(card_id: int, title: str) -> str:
    """What the model is asked after /card: a short confirmation, not the work itself."""
    return (
        f"The user made card #{card_id} on the project board from their request, titled: {title}\n\n"
        "The card is in Design; its next step is its plan, which the user starts with Plan on the card. Reply in one "
        "or two sentences in the user's language: say that the card was made and give its number. Do not start the "
        "work and do not call any tools."
    )


def card_command(
    board: BoardService | None, session: Session, conversation: Conversation, text: str
) -> dict[str, Any] | None:
    """/card as a built-in command: the card is made now; the reply only confirms it, read only. None when the
    message is not /card."""
    match = CARD_COMMAND.match(text.strip())
    if match is None:
        return None
    if conversation.project_id is None:
        raise CommandRefused("/card works in a project conversation.")
    if board is None:
        raise CommandRefused("The project board is not available.")
    request = (match.group(1) or "").strip()
    if not request:
        raise CommandRefused("Say what the card is for: /card <title or request>.")
    # The board writes its index in sessions of its own and SQLite has one writer at a time, so what this session
    # changed so far is committed first.
    session.commit()
    try:
        detail = make_card(board, conversation.project_id, conversation.id, request, f"conversation:{conversation.id}")
    except BoardError as error:
        raise CommandRefused(str(error)) from error
    return {
        "type": "command",
        "name": "card",
        "text": card_reply_text(detail.view.id, detail.view.title),
        "read_only": True,
        "card_id": detail.view.id,
    }


def card_from_message(
    board: BoardService, session_factory: sessionmaker[Session], conversation_id: int, message_id: int
) -> CardDetail:
    """*Make a card* on a message of a project conversation: the message's text becomes the card. Raises a
    BoardError."""
    with session_factory() as session:
        conversation = session.get(Conversation, conversation_id)
        message = session.get(Message, message_id)
        if conversation is None or message is None or message.conversation_id != conversation_id:
            raise InvalidCard("That message is not in this conversation.")
        if conversation.project_id is None:
            raise InvalidCard("A card can be made from a message of a project conversation.")
        project_id, text = conversation.project_id, message_text(message)
    if not text:
        raise InvalidCard("The message has no text to make a card of.")
    return make_card(board, project_id, conversation_id, text, f"conversation:{conversation_id}/message:{message_id}")
