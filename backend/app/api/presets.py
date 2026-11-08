"""Model presets per environment."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import EnvironmentDependency, ProviderRegistryDependency, SessionDependency
from app.agent.workers import list_workers
from app.api.schemas import (
    PresetCreate,
    PresetOut,
    PresetRouteOut,
    PresetUpdate,
    PresetVendorOut,
    PresetVendorSwitch,
    PresetVendorSwitched,
    VendorOut,
    WorkerOut,
)
from app.core.routing import ROUTE_KINDS, preset_routes
from app.presets.service import (
    PresetError,
    change_routing,
    create_preset,
    delete_preset,
    list_presets,
    plain_chat_preset,
    update_preset,
)
from app.presets.vendor import VendorState, switch_vendor, vendor_state
from app.storage.models import ModelPreset

router = APIRouter(tags=["presets"])


def preset_out(preset: ModelPreset, plain_chat: ModelPreset | None) -> PresetOut:
    """The preset as the API shows it; plain_chat is the preset conversations outside a project start with."""
    return PresetOut(
        id=preset.id,
        environment_id=preset.environment_id,
        name=preset.name,
        model=preset.model,
        effort=preset.effort,
        is_default=preset.is_default,
        worker_model=preset.worker_model,
        worker_effort=preset.worker_effort,
        utility_model=preset.utility_model,
        utility_effort=preset.utility_effort,
        max_output_tokens=preset.max_output_tokens,
        plain_chats=plain_chat is not None and plain_chat.id == preset.id,
        routing=preset.routing,
        routes=[
            PresetRouteOut(kind=kind, label=ROUTE_KINDS[kind], role=role)
            for kind, role in preset_routes(preset.routes).items()
        ],
    )


ROLE_FIELDS = ("worker_model", "worker_effort", "utility_model", "utility_effort")


def get_preset(preset_id: int, session: SessionDependency) -> ModelPreset:
    preset = session.get(ModelPreset, preset_id)
    if preset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Preset not found.")
    return preset


PresetDependency = Annotated[ModelPreset, Depends(get_preset)]


@router.get("/environments/{environment_id}/presets", response_model=list[PresetOut])
def read_presets(environment: EnvironmentDependency, session: SessionDependency) -> list[PresetOut]:
    plain_chat = plain_chat_preset(session, environment.id)
    return [preset_out(preset, plain_chat) for preset in list_presets(session, environment.id)]


@router.post(
    "/environments/{environment_id}/presets", response_model=PresetOut, status_code=status.HTTP_201_CREATED
)
def add_preset(
    payload: PresetCreate,
    environment: EnvironmentDependency,
    session: SessionDependency,
    providers: ProviderRegistryDependency,
) -> PresetOut:
    try:
        roles = {field: getattr(payload, field) for field in ROLE_FIELDS}
        preset = create_preset(
            session, providers, environment.id, payload.name, payload.model, payload.effort, roles, payload.max_output_tokens
        )
    except PresetError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    return preset_out(preset, plain_chat_preset(session, environment.id))


@router.patch("/presets/{preset_id}", response_model=PresetOut)
def change_preset(
    payload: PresetUpdate, preset: PresetDependency, session: SessionDependency, providers: ProviderRegistryDependency
) -> PresetOut:
    try:
        roles = {field: getattr(payload, field) for field in ROLE_FIELDS if field in payload.model_fields_set}
        update_preset(
            session,
            providers,
            preset,
            payload.name,
            payload.model,
            payload.effort,
            payload.is_default,
            roles,
            payload.max_output_tokens if "max_output_tokens" in payload.model_fields_set else ...,
        )
        if payload.routing is not None or payload.routes is not None:
            change_routing(session, preset, payload.routing, dict(payload.routes) if payload.routes else None)
    except PresetError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    return preset_out(preset, plain_chat_preset(session, preset.environment_id))


def vendor_out(state: VendorState) -> PresetVendorOut:
    return PresetVendorOut(
        vendors=[VendorOut(name=vendor.name, label=vendor.label, models=vendor.models) for vendor in state.vendors],
        current=state.current,
    )


@router.get("/environments/{environment_id}/presets/vendor", response_model=PresetVendorOut)
def read_preset_vendor(
    environment: EnvironmentDependency, session: SessionDependency, providers: ProviderRegistryDependency
) -> PresetVendorOut:
    """Which vendor the environment's presets use, and the vendors they can switch to."""
    return vendor_out(vendor_state(session, providers, environment.id))


@router.put("/environments/{environment_id}/presets/vendor", response_model=PresetVendorSwitched)
def change_preset_vendor(
    payload: PresetVendorSwitch,
    environment: EnvironmentDependency,
    session: SessionDependency,
    providers: ProviderRegistryDependency,
) -> PresetVendorSwitched:
    """Move the presets and the utility model of the environment to the vendor's models of the same size."""
    try:
        changed = switch_vendor(session, providers, environment.id, payload.vendor)
    except PresetError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    state = vendor_out(vendor_state(session, providers, environment.id))
    return PresetVendorSwitched(vendors=state.vendors, current=state.current, changed=changed)


@router.get("/environments/{environment_id}/workers", response_model=list[WorkerOut])
def read_workers(
    environment: EnvironmentDependency, session: SessionDependency, providers: ProviderRegistryDependency
) -> list[WorkerOut]:
    """Who `delegate` can hand a task to in this environment: each preset's worker model, by the
    preset's name, and the coding agent CLIs claude_code and codex with the model of their setting."""
    return [WorkerOut(**vars(choice)) for choice in list_workers(session, providers, environment.id)]


@router.delete("/presets/{preset_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_preset(preset: PresetDependency, session: SessionDependency) -> None:
    try:
        delete_preset(session, preset)
    except PresetError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
