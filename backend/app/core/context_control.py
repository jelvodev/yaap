"""Context control: the context limit, when YAAP compacts, and the length of one reply.

The context limit is how large YAAP lets a conversation's context grow: trimming old tool output and summarising are
measured against it instead of against the model's window. The model keeps its full window, so a reply never fails on
the limit. The limit comes from the conversation's own choice, else from the `context_limit` setting (project,
environment, global), else it is the model's window; it is never more than the window.

Compaction: with `auto_compact` on, old tool output is shortened at `trim_at` percent of the limit and the
earlier messages are summarised at `compact_at` percent. YAAP compacts every connection except Claude Code and Codex,
which compact their own sessions.

The length of one reply (`max_tokens` in the provider request) comes from the preset, else it is the model's maximum.
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.providers.catalog import DEFAULT_MAX_OUTPUT
from app.providers.registry import ProviderRegistry
from app.storage.models import Conversation

# The setting value that means "as large as the model's window"; the default.
WINDOW = "window"
MIN_CONTEXT_LIMIT = 16_000
MAX_CONTEXT_LIMIT = 10_000_000
MIN_OUTPUT_TOKENS = 1_024
DEFAULT_COMPACT_AT = 70
DEFAULT_TRIM_AT = 50
# Connections that keep their own session and compact it themselves; YAAP leaves those alone.
SELF_COMPACTING = frozenset({"claude_code", "codex"})


def is_token_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_context_limit(value: Any, _providers: ProviderRegistry) -> str | None:
    if value == WINDOW or (is_token_count(value) and MIN_CONTEXT_LIMIT <= value <= MAX_CONTEXT_LIMIT):
        return None
    return f'Use "{WINDOW}" for the model\'s window, or a number of tokens from {MIN_CONTEXT_LIMIT:,} up.'


def check_conversation_limit(providers: ProviderRegistry, model_id: str, value: Any) -> str | None:
    """Why a conversation's own limit is not valid for its model, or None. None as value follows the settings."""
    if value is None:
        return None
    model = providers.find_model(model_id)
    window = model.context_window if model else MAX_CONTEXT_LIMIT
    if not is_token_count(value) or not min(MIN_CONTEXT_LIMIT, window) <= value <= window:
        return f"The context limit is a number of tokens from {min(MIN_CONTEXT_LIMIT, window):,} up to the model's window ({window:,})."
    return None


def validate_share(value: Any, _providers: ProviderRegistry) -> str | None:
    if is_token_count(value) and 10 <= value <= 95:
        return None
    return "Use a percentage of the context limit from 10 to 95."


@dataclass(frozen=True)
class ContextControl:
    """How large a conversation may grow, measured in tokens of context."""

    # The model's window, or None for an unknown model.
    window: int | None
    # The limit in force: at most the window; None when neither is known.
    limit: int | None
    # Where the limit comes from: conversation, project, environment, global, or default (the model's window).
    source: str
    # Whether YAAP compacts by itself, and at which percentage of the limit it summarises and trims.
    auto_compact: bool = True
    compact_at: int = DEFAULT_COMPACT_AT
    trim_at: int = DEFAULT_TRIM_AT
    # Claude Code and Codex compact their own sessions; YAAP neither trims nor summarises them.
    compacts_itself: bool = False

    def reached(self, percent: int, used: int | None) -> bool:
        """Whether YAAP compacts by itself and the context used reached percent of the limit."""
        if not self.auto_compact or self.compacts_itself or used is None or not self.limit:
            return False
        return used >= self.limit * percent / 100

    def needs_trim(self, used: int | None) -> bool:
        return self.reached(self.trim_at, used)

    def needs_summary(self, used: int | None) -> bool:
        return self.reached(self.compact_at, used)


def setting_limit(value: Any, window: int | None) -> int | None:
    """A context_limit setting as tokens for a model: the window for "window", never more than the window."""
    if not is_token_count(value):
        return window
    return min(value, window) if window else value


def resolve_context_control(session: Session, providers: ProviderRegistry, conversation: Conversation) -> ContextControl:
    from app.core.settings import SETTING_DEFINITIONS, resolve_setting

    def setting(key: str) -> Any:
        return resolve_setting(session, SETTING_DEFINITIONS[key], conversation.environment_id, conversation.project_id)

    model = providers.find_model(conversation.model)
    window = model.context_window if model and model.context_window else None
    if conversation.context_limit:
        limit = min(conversation.context_limit, window) if window else conversation.context_limit
        source = "conversation"
    else:
        resolved = setting("context_limit")
        limit = setting_limit(resolved.value, window)
        source = "default" if resolved.value == WINDOW else resolved.source
    compact_at, trim_at = setting("compact_at").value, setting("trim_at").value
    return ContextControl(
        window,
        limit,
        source,
        auto_compact=setting("auto_compact").value != "off",
        compact_at=compact_at if is_token_count(compact_at) else DEFAULT_COMPACT_AT,
        trim_at=trim_at if is_token_count(trim_at) else DEFAULT_TRIM_AT,
        compacts_itself=compacts_itself(session, providers, conversation),
    )


def compacts_itself(session: Session, providers: ProviderRegistry, conversation: Conversation) -> bool:
    """Whether the conversation's connection keeps and compacts its own session (Claude Code, Codex)."""
    from app.core.settings import resolve_connection

    connection = resolve_connection(session, conversation.provider, conversation.environment_id)
    spec = providers.find_provider(conversation.provider, connection)
    return spec is not None and spec.connection in SELF_COMPACTING


def validate_output_tokens(providers: ProviderRegistry, model_id: str, value: int | None) -> str | None:
    """Why a preset's reply length does not fit its model, or None. None is the model's maximum."""
    if value is None:
        return None
    model = providers.find_model(model_id)
    maximum = model.max_output_tokens if model else None
    if not is_token_count(value) or value < MIN_OUTPUT_TOKENS or (maximum is not None and value > maximum):
        upper = f" up to the model's maximum ({maximum:,})" if maximum is not None else ""
        return f"The longest reply is a number of tokens from {MIN_OUTPUT_TOKENS:,}{upper}."
    return None


def output_tokens(
    session: Session, providers: ProviderRegistry, environment_id: int, preset_name: str | None, model_id: str
) -> int:
    """The reply length for a request to model_id: the preset's choice, never more than the model's maximum."""
    from app.presets.service import find_preset_by_name

    model = providers.find_model(model_id)
    maximum = model.max_output_tokens if model else DEFAULT_MAX_OUTPUT
    preset = find_preset_by_name(session, environment_id, preset_name) if preset_name else None
    if preset is not None and preset.max_output_tokens:
        return min(preset.max_output_tokens, maximum)
    return maximum
