"""Conversations that API tokens start: A2A tasks and the log of an outside MCP client.

Such a conversation lives in the token's environment and project, starts with the project's default preset (outside a
project the plain-chat preset; else the environment's default preset) and has the access *as set*, so the permission settings apply; nothing gives it full access.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.presets.service import starting_preset
from app.providers.registry import ProviderRegistry
from app.storage.models import Conversation, Environment, Project


class ScopeError(Exception):
    """The token's scope cannot be used (gone, or no model); the message is safe to show to the caller."""


@dataclass(frozen=True)
class Scope:
    environment: Environment
    project: Project | None


def token_scope(session: Session, environment_id: int, project_id: int | None) -> Scope:
    environment = session.get(Environment, environment_id)
    if environment is None:
        raise ScopeError("The environment of this token no longer exists.")
    project = None
    if project_id is not None:
        project = session.get(Project, project_id)
        if project is None or project.environment_id != environment.id:
            raise ScopeError("The project of this token no longer exists.")
    return Scope(environment, project)


def in_scope(conversation: Conversation | None, environment_id: int, project_id: int | None) -> bool:
    return (
        conversation is not None
        and conversation.environment_id == environment_id
        and conversation.project_id == project_id
    )


def new_conversation(session: Session, providers: ProviderRegistry, scope: Scope, title: str) -> Conversation:
    """A conversation in the scope with its default model; added and flushed, not committed."""
    preset = starting_preset(session, scope.environment.id, scope.project)
    if preset is None:
        raise ScopeError("The environment has no model preset to work with.")
    model = providers.find_model(preset.model)
    if model is None:
        raise ScopeError(f"The model {preset.model} of the preset {preset.name} is not available.")
    now = datetime.now(UTC)
    conversation = Conversation(
        environment_id=scope.environment.id,
        project_id=scope.project.id if scope.project is not None else None,
        agent=None,
        pinned_skills=[],
        preset=preset.name,
        title=title[:200],
        provider=model.provider,
        model=model.id,
        effort=preset.effort if preset.effort in model.efforts else model.efforts[0],
        access="default",
        created_at=now,
        updated_at=now,
    )
    session.add(conversation)
    session.flush()
    return conversation
