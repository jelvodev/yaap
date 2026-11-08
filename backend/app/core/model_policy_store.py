"""Reading the model policy of an environment and a project from the settings (core/model_policy.py)."""

from typing import Any

from sqlalchemy.orm import Session

from app.core.model_policy import (
    LevelPolicy,
    ModelPolicy,
    PolicySource,
    parse_level,
    policy_choices,
    widening_problem,
)
from app.core.settings import find_setting_row
from app.providers.registry import ProviderRegistry
from app.storage.models import Environment, Project

SETTING_KEY = "model_policy"


def stored_level(session: Session, environment_id: int, project_id: int | None = None) -> LevelPolicy | None:
    row = find_setting_row(session, SETTING_KEY, environment_id, project_id)
    return parse_level(row.value) if row is not None else None


def resolve_model_policy(session: Session, environment_id: int, project_id: int | None = None) -> ModelPolicy:
    """The policies a request in this environment, and in this project when given, must pass."""
    levels: list[tuple[PolicySource, LevelPolicy]] = []
    environment_level = stored_level(session, environment_id)
    if environment_level is not None:
        environment = session.get(Environment, environment_id)
        name = environment.name if environment else str(environment_id)
        levels.append((PolicySource("environment", name), environment_level))
    if project_id is not None and (project_level := stored_level(session, environment_id, project_id)) is not None:
        project = session.get(Project, project_id)
        levels.append((PolicySource("project", project.name if project else str(project_id)), project_level))
    return ModelPolicy(tuple(levels))


def project_widening(session: Session, providers: ProviderRegistry, project: Project, value: Any) -> str | None:
    """Why a project's new policy would widen its environment's, or None."""
    environment_level = stored_level(session, project.environment_id)
    if environment_level is None:
        return None
    environment = session.get_one(Environment, project.environment_id)
    return widening_problem(environment_level, parse_level(value), environment.name, policy_choices(providers))
