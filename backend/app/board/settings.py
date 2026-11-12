"""The board's settings: their keys and validation. Kept free of imports from `app.core.settings`, which uses them."""

from typing import Any

from app.providers.registry import ProviderRegistry

PROJECT_BOARD_KEY = "project_board"
BOARD_PUSH_KEY = "board_push"


def validate_switch(value: Any, _providers: ProviderRegistry) -> str | None:
    """A switch is exactly true or false."""
    return None if isinstance(value, bool) else "Use true or false."
