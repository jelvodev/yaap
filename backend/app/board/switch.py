"""Whether a project has its board on, and whether pushing it is offered."""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.board.settings import BOARD_PUSH_KEY, PROJECT_BOARD_KEY
from app.core.settings import SETTING_DEFINITIONS, Source, resolve_setting


@dataclass(frozen=True)
class BoardSwitch:
    enabled: bool
    source: Source


def board_enabled(session: Session, environment_id: int, project_id: int) -> BoardSwitch:
    resolved = resolve_setting(session, SETTING_DEFINITIONS[PROJECT_BOARD_KEY], environment_id, project_id)
    return BoardSwitch(enabled=resolved.value is True, source=resolved.source)


def board_push_allowed(session: Session, environment_id: int, project_id: int) -> bool:
    return resolve_setting(session, SETTING_DEFINITIONS[BOARD_PUSH_KEY], environment_id, project_id).value is True


def board_tools_offered(enabled: bool, linked_card: bool) -> bool:
    """Card tools are offered only in a conversation linked to a card while the board is on."""
    return enabled and linked_card
