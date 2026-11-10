"""Keep the registry's OpenAI-compatible endpoints in line with the global setting `openai_endpoints`."""

from sqlalchemy.orm import Session

from app.core.settings import SETTING_DEFINITIONS, resolve_setting
from app.providers.endpoints import SETTING_KEY, parse_endpoints, refresh_endpoints
from app.providers.registry import ProviderRegistry


def sync_endpoints(session: Session, providers: ProviderRegistry, force: bool = False) -> None:
    """Read the setting and bring the endpoint providers and their models up to date (at most every minute unless
    the setting changed or `force` is set)."""
    value = resolve_setting(session, SETTING_DEFINITIONS[SETTING_KEY], None).value
    refresh_endpoints(providers, parse_endpoints(value), force)
