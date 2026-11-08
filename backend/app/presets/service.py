"""Model presets: named model and effort combinations per environment."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.context_control import validate_output_tokens
from app.core.routing import check_routes, preset_routes
from app.core.settings import find_setting_row, resolve_text
from app.providers.registry import ProviderRegistry
from app.storage.models import ModelPreset, Project, Setting

from dataclasses import dataclass
from types import EllipsisType
from typing import Literal

DEFAULT_PRESETS = (
    ("Fast", "claude-sonnet-5-5", "low", False),
    ("Balanced", "claude-opus-5-5", "medium", True),
    ("Deep", "claude-opus-5-5", "high", False),
    ("Cheap", "claude-haiku-5-5", "low", False),
)
# Worker models of the seeded presets: delegated work runs on Sonnet.
DEFAULT_WORKERS = {"Balanced": ("claude-sonnet-5-5", "low"), "Deep": ("claude-sonnet-5-5", "medium")}

Role = Literal["main", "worker", "utility"]
# The environment setting naming the preset of conversations outside a project.
PLAIN_CHAT_SETTING = "plain_chat_preset"


@dataclass(frozen=True)
class RoleModel:
    model: str
    effort: str


class PresetError(Exception):
    """A preset change that cannot be made; the message says why."""


def list_presets(session: Session, environment_id: int) -> list[ModelPreset]:
    return list(
        session.scalars(
            select(ModelPreset).where(ModelPreset.environment_id == environment_id).order_by(ModelPreset.position)
        )
    )


def seed_presets(session: Session, environment_id: int) -> None:
    for position, (name, model, effort, is_default) in enumerate(DEFAULT_PRESETS):
        session.add(
            ModelPreset(
                environment_id=environment_id,
                name=name,
                model=model,
                effort=effort,
                position=position,
                is_default=is_default,
                worker_model=DEFAULT_WORKERS.get(name, (None, None))[0],
                worker_effort=DEFAULT_WORKERS.get(name, (None, None))[1],
            )
        )


def find_preset_by_name(session: Session, environment_id: int, name: str) -> ModelPreset | None:
    return session.scalar(
        select(ModelPreset).where(
            ModelPreset.environment_id == environment_id, func.lower(ModelPreset.name) == name.strip().lower()
        )
    )


def check_model(providers: ProviderRegistry, model_id: str, effort: str) -> None:
    model = providers.find_model(model_id)
    if model is None:
        raise PresetError("Unknown model.")
    if effort not in model.efforts:
        raise PresetError(f"Effort must be one of: {', '.join(model.efforts)}.")


def check_name(session: Session, environment_id: int, name: str, current_id: int | None = None) -> str:
    cleaned = " ".join(name.split())
    if not cleaned or len(cleaned) > 40:
        raise PresetError("A preset name has 1 to 40 characters.")
    existing = find_preset_by_name(session, environment_id, cleaned)
    if existing is not None and existing.id != current_id:
        raise PresetError(f"There is already a preset named {existing.name}.")
    return cleaned


def check_output_tokens(providers: ProviderRegistry, model: str, value: int | None) -> None:
    if (problem := validate_output_tokens(providers, model, value)) is not None:
        raise PresetError(problem)


def make_default(session: Session, preset: ModelPreset) -> None:
    for other in list_presets(session, preset.environment_id):
        other.is_default = other.id == preset.id


def check_role(providers: ProviderRegistry, model: str | None, effort: str | None) -> tuple[str | None, str | None]:
    """A role's model and effort, both empty or both valid; an empty effort becomes the model's default."""
    if not model:
        return None, None
    found = providers.find_model(model)
    if found is None:
        raise PresetError("Unknown model.")
    effort = effort or found.default_effort
    check_model(providers, model, effort)
    return model, effort


def create_preset(
    session: Session,
    providers: ProviderRegistry,
    environment_id: int,
    name: str,
    model: str,
    effort: str,
    roles: dict[str, str | None] | None = None,
    max_output_tokens: int | None = None,
) -> ModelPreset:
    check_model(providers, model, effort)
    check_output_tokens(providers, model, max_output_tokens)
    roles = roles or {}
    worker = check_role(providers, roles.get("worker_model"), roles.get("worker_effort"))
    utility = check_role(providers, roles.get("utility_model"), roles.get("utility_effort"))
    existing = list_presets(session, environment_id)
    preset = ModelPreset(
        environment_id=environment_id,
        name=check_name(session, environment_id, name),
        model=model,
        effort=effort,
        position=max((item.position for item in existing), default=-1) + 1,
        is_default=not existing,
        worker_model=worker[0],
        worker_effort=worker[1],
        utility_model=utility[0],
        utility_effort=utility[1],
        max_output_tokens=max_output_tokens,
    )
    session.add(preset)
    session.commit()
    return preset


def update_preset(
    session: Session,
    providers: ProviderRegistry,
    preset: ModelPreset,
    name: str | None,
    model: str | None,
    effort: str | None,
    is_default: bool | None,
    roles: dict[str, str | None] | None = None,
    max_output_tokens: int | None | EllipsisType = ...,
) -> ModelPreset:
    """Change the given fields. `roles` holds only the role fields that were sent; an empty model clears the role.
    max_output_tokens changes only when given; None returns to the model's maximum."""
    roles = roles or {}
    for role in ("worker", "utility"):
        model_key, effort_key = f"{role}_model", f"{role}_effort"
        if model_key not in roles and effort_key not in roles:
            continue
        model_value = roles[model_key] if model_key in roles else getattr(preset, model_key)
        # A new model without an effort takes that model's default effort.
        effort_value = roles[effort_key] if effort_key in roles else None if model_key in roles else getattr(preset, effort_key)
        checked = check_role(providers, model_value, effort_value)
        setattr(preset, model_key, checked[0])
        setattr(preset, effort_key, checked[1])
    if name is not None:
        previous = preset.name
        preset.name = check_name(session, preset.environment_id, name, preset.id)
        follow_rename(session, preset.environment_id, previous, preset.name)
    if model is not None or effort is not None:
        check_model(providers, model or preset.model, effort or preset.effort)
        preset.model = model or preset.model
        preset.effort = effort or preset.effort
    if max_output_tokens is not ...:
        check_output_tokens(providers, preset.model, max_output_tokens)
        preset.max_output_tokens = max_output_tokens
    elif model is not None and preset.max_output_tokens:
        # A model with a lower maximum keeps the preset within it.
        found = providers.find_model(preset.model)
        if found is not None and preset.max_output_tokens > found.max_output_tokens:
            preset.max_output_tokens = found.max_output_tokens
    if is_default:
        make_default(session, preset)
    session.commit()
    return preset


def change_routing(session: Session, preset: ModelPreset, routing: bool | None, routes: dict[str, str] | None) -> None:
    """Turn routing on or off and change which role serves which kind of message; None leaves a part as it is."""
    if routes is not None:
        if (problem := check_routes(routes)) is not None:
            raise PresetError(problem)
        preset.routes = {**preset_routes(preset.routes), **routes}
    if routing is not None:
        preset.routing = routing
    session.commit()


def delete_preset(session: Session, preset: ModelPreset) -> None:
    remaining = [item for item in list_presets(session, preset.environment_id) if item.id != preset.id]
    if not remaining:
        raise PresetError("An environment keeps at least one preset.")
    if preset.is_default:
        remaining[0].is_default = True
    plain_chat = plain_chat_row(session, preset.environment_id, preset.name)
    if plain_chat is not None:
        session.delete(plain_chat)
    session.delete(preset)
    session.commit()


def default_preset(session: Session, environment_id: int) -> ModelPreset | None:
    presets = list_presets(session, environment_id)
    return next((preset for preset in presets if preset.is_default), presets[0] if presets else None)


def plain_chat_row(session: Session, environment_id: int, name: str) -> Setting | None:
    """The environment's plain-chat setting when it names this preset."""
    row = find_setting_row(session, PLAIN_CHAT_SETTING, environment_id)
    return row if row is not None and str(row.value or "").strip().lower() == name.lower() else None


def follow_rename(session: Session, environment_id: int, previous: str, name: str) -> None:
    """A renamed preset stays the one plain chats start with; committed with the rename."""
    row = plain_chat_row(session, environment_id, previous)
    if row is not None and previous != name:
        row.value = name


def plain_chat_preset(session: Session, environment_id: int) -> ModelPreset | None:
    """The preset the environment's plain-chat setting names; None when it is empty or names no preset."""
    name = resolve_text(session, PLAIN_CHAT_SETTING, environment_id).strip()
    return find_preset_by_name(session, environment_id, name) if name else None


def starting_preset(session: Session, environment_id: int, project: Project | None) -> ModelPreset | None:
    """The preset a new conversation starts with: in a project the project's preset, outside one the plain-chat
    preset; either falls back to the environment's default preset."""
    if project is not None:
        named = find_preset_by_name(session, environment_id, project.default_preset) if project.default_preset else None
    else:
        named = plain_chat_preset(session, environment_id)
    return named or default_preset(session, environment_id)


def role_model(session: Session, environment_id: int, preset_name: str | None, role: Role) -> RoleModel | None:
    """The model for a role: from the named preset, else from the environment's default preset; None when neither has one.

    The utility role then falls back to the utility_model setting (core.utility); the worker role to the main model.
    """
    candidates = []
    if preset_name:
        named = find_preset_by_name(session, environment_id, preset_name)
        if named is not None:
            candidates.append(named)
    fallback = default_preset(session, environment_id)
    if fallback is not None and fallback not in candidates:
        candidates.append(fallback)
    for preset in candidates:
        if role == "main":
            return RoleModel(preset.model, preset.effort)
        model = getattr(preset, f"{role}_model")
        if model:
            return RoleModel(model, getattr(preset, f"{role}_effort") or "low")
    return None
