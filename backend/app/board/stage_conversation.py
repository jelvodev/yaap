"""The conversations a card's stages run in: made in the card's project and started with a first message, through the
same reply service a message from the user goes through (so the permission settings, the budget and /plan apply as
they do in the chat)."""

from collections.abc import Callable
from dataclasses import dataclass

from app.board.errors import BoardUnavailable
from app.core.api_conversations import Scope, ScopeError, new_conversation
from app.core.reply_errors import ReplyError
from app.core.reply_service import NewMessage, ReplyService
from app.core.reply_services import ReplyServices
from app.storage.models import Conversation, Environment, Project


@dataclass(frozen=True)
class ReplyStarter:
    """A ConversationStarter (board/route.py) on YAAP's reply service."""

    services: Callable[[], ReplyServices]

    def create(self, project_id: int, title: str) -> int:
        services = self.services()
        with services.session_factory() as session:
            project = session.get_one(Project, project_id)
            scope = Scope(session.get_one(Environment, project.environment_id), project)
            try:
                conversation = new_conversation(session, services.providers, scope, title)
            except ScopeError as error:
                raise BoardUnavailable(str(error)) from error
            session.commit()
            return conversation.id

    def send(self, conversation_id: int, text: str) -> None:
        """Send the first message; the reply runs in the background. Blocking: call it from a worker thread."""
        services = self.services()
        with services.session_factory() as session:
            conversation = session.get_one(Conversation, conversation_id)
            try:
                ReplyService(services).send(session, conversation, NewMessage(text))
            except ReplyError as error:
                raise BoardUnavailable(str(error)) from error

    def busy(self, conversation_id: int) -> bool:
        return self.services().replies.busy(conversation_id)
