"""Settings per level. Global values are inherited by environments unless an environment overrides them."""

from typing import Any

from fastapi import APIRouter, HTTPException, Response, status

from app.api.dependencies import (
    EnvironmentDependency,
    ProjectDependency,
    ProviderRegistryDependency,
    SessionDependency,
)
from app.agent.policy import CONFIGURABLE_TOOLS
from app.api.schemas import SettingOption, SettingOut, SettingValue, ToolPermissionOut
from app.core.endpoint_sync import sync_endpoints
from app.core.model_policy_store import SETTING_KEY as MODEL_POLICY
from app.core.model_policy_store import project_widening
from app.core.settings import (
    SETTING_DEFINITIONS,
    ResolvedSetting,
    SettingDefinition,
    clear_setting,
    resolve_setting,
    store_setting,
)
from app.providers.endpoints import SETTING_KEY as ENDPOINTS_KEY
from app.providers.registry import ProviderRegistry

router = APIRouter(tags=["settings"])


def setting_to_out(resolved: ResolvedSetting) -> SettingOut:
    return SettingOut(
        key=resolved.definition.key,
        label=resolved.definition.label,
        description=resolved.definition.description,
        options=[SettingOption(value=option.value, label=option.label) for option in resolved.definition.options],
        value=resolved.value,
        source=resolved.source,
        inherited_value=resolved.inherited_value,
        inherited_source=resolved.inherited_source,
        level_value=resolved.level_value,
    )


def require_definition(key: str, level: str) -> SettingDefinition:
    definition = SETTING_DEFINITIONS.get(key)
    if definition is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown setting.")
    if level not in definition.levels:
        allowed = " or ".join(sorted(definition.levels))
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"'{definition.label}' cannot be set at the {level} level, only at the {allowed} level.",
        )
    return definition


def validated(definition: SettingDefinition, value: Any, providers: ProviderRegistry) -> Any:
    if isinstance(value, str):
        value = value.strip()
    elif isinstance(value, list):
        value = [item.strip() if isinstance(item, str) else item for item in value]
        value = [item for item in value if item != ""]
    problem = definition.check(value, providers)
    if problem:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, problem)
    return value


@router.get("/settings", response_model=list[SettingOut])
def list_global_settings(session: SessionDependency) -> list[SettingOut]:
    return [
        setting_to_out(resolve_setting(session, definition, None))
        for definition in SETTING_DEFINITIONS.values()
        if "global" in definition.levels
    ]


@router.put("/settings/{key}", response_model=SettingOut)
def set_global_setting(
    key: str, payload: SettingValue, session: SessionDependency, providers: ProviderRegistryDependency
) -> SettingOut:
    definition = require_definition(key, "global")
    store_setting(session, definition, None, validated(definition, payload.value, providers))
    if key == ENDPOINTS_KEY:
        sync_endpoints(session, providers)
    return setting_to_out(resolve_setting(session, definition, None))


@router.delete("/settings/{key}", status_code=status.HTTP_204_NO_CONTENT)
def reset_global_setting(key: str, session: SessionDependency, providers: ProviderRegistryDependency) -> Response:
    clear_setting(session, require_definition(key, "global"), None)
    if key == ENDPOINTS_KEY:
        sync_endpoints(session, providers)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/environments/{environment_id}/settings", response_model=list[SettingOut])
def list_environment_settings(environment: EnvironmentDependency, session: SessionDependency) -> list[SettingOut]:
    return [
        setting_to_out(resolve_setting(session, definition, environment.id))
        for definition in SETTING_DEFINITIONS.values()
        if "environment" in definition.levels
    ]


@router.put("/environments/{environment_id}/settings/{key}", response_model=SettingOut)
def override_environment_setting(
    key: str,
    payload: SettingValue,
    environment: EnvironmentDependency,
    session: SessionDependency,
    providers: ProviderRegistryDependency,
) -> SettingOut:
    definition = require_definition(key, "environment")
    store_setting(session, definition, environment.id, validated(definition, payload.value, providers))
    return setting_to_out(resolve_setting(session, definition, environment.id))


@router.delete("/environments/{environment_id}/settings/{key}", response_model=SettingOut)
def reset_environment_setting(key: str, environment: EnvironmentDependency, session: SessionDependency) -> SettingOut:
    definition = require_definition(key, "environment")
    clear_setting(session, definition, environment.id)
    return setting_to_out(resolve_setting(session, definition, environment.id))


@router.get("/projects/{project_id}/settings", response_model=list[SettingOut])
def list_project_settings(project: ProjectDependency, session: SessionDependency) -> list[SettingOut]:
    return [
        setting_to_out(resolve_setting(session, definition, project.environment_id, project.id))
        for definition in SETTING_DEFINITIONS.values()
        if "project" in definition.levels
    ]


@router.put("/projects/{project_id}/settings/{key}", response_model=SettingOut)
def override_project_setting(
    key: str,
    payload: SettingValue,
    project: ProjectDependency,
    session: SessionDependency,
    providers: ProviderRegistryDependency,
) -> SettingOut:
    definition = require_definition(key, "project")
    value = validated(definition, payload.value, providers)
    if key == MODEL_POLICY and (problem := project_widening(session, providers, project, value)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, problem)
    store_setting(session, definition, project.environment_id, value, project.id)
    return setting_to_out(resolve_setting(session, definition, project.environment_id, project.id))


@router.delete("/projects/{project_id}/settings/{key}", response_model=SettingOut)
def reset_project_setting(key: str, project: ProjectDependency, session: SessionDependency) -> SettingOut:
    definition = require_definition(key, "project")
    clear_setting(session, definition, project.environment_id, project.id)
    return setting_to_out(resolve_setting(session, definition, project.environment_id, project.id))


@router.get("/tool-permissions", response_model=list[ToolPermissionOut])
def configurable_tools() -> list[ToolPermissionOut]:
    """The tools whose permission can be changed, with the permission they have by default."""
    from app.agent.tools import TOOLS_BY_NAME

    return [
        ToolPermissionOut(name=name, label=label, default=TOOLS_BY_NAME[name].permission)
        for name, label in CONFIGURABLE_TOOLS.items()
    ]
