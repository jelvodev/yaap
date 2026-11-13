"""Typed lookups of resolved settings that other parts of the application ask for."""

from sqlalchemy.orm import Session

from app.agent.policy import ToolPolicy
from app.core.prompts import ENGLISH, Languages
from app.core.settings.definitions import SETTING_DEFINITIONS
from app.core.settings.setting_types import connection_setting_key
from app.core.settings.storage import resolve_setting


def resolve_tool_policy(session: Session, environment_id: int, project_id: int | None) -> ToolPolicy:
    """What the agent may do without asking in this environment or project."""

    def value(key: str) -> object:
        return resolve_setting(session, SETTING_DEFINITIONS[key], environment_id, project_id).value

    permissions = value("tool_permissions")
    commands = value("command_allowlist")
    domains = value("web_allowlist")
    return ToolPolicy(
        permissions=dict(permissions) if isinstance(permissions, dict) else {},
        commands=tuple(commands) if isinstance(commands, list) else (),
        domains=tuple(domain.lower().strip() for domain in domains) if isinstance(domains, list) else (),
    )


def resolve_text(session: Session, key: str, environment_id: int | None) -> str:
    return str(resolve_setting(session, SETTING_DEFINITIONS[key], environment_id).value or "")


def resolve_languages(session: Session, environment_id: int | None, project_id: int | None = None) -> Languages:
    """The reply and work language at the nearest level."""
    reply = resolve_setting(session, SETTING_DEFINITIONS["reply_language"], environment_id, project_id).value
    work = resolve_setting(session, SETTING_DEFINITIONS["work_language"], environment_id, project_id).value
    return Languages(reply=str(reply or "").strip(), work=str(work or "").strip() or ENGLISH)


def resolve_connection(session: Session, provider: str, environment_id: int) -> str | None:
    """The connection an environment uses for a provider, or None when the provider has only one."""
    definition = SETTING_DEFINITIONS.get(connection_setting_key(provider))
    if definition is None:
        return None
    return str(resolve_setting(session, definition, environment_id).value)
