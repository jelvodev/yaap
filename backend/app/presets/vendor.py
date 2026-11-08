"""The vendor an environment's presets use: Claude or OpenAI by default, switched in one step.

Switching moves every model of another vendor in the environment's presets (main, worker and utility role) to the
chosen vendor's model of the same size (`ModelInfo.size`), and does the same for the environment's utility model.
A model without a size, such as a local Ollama model, stays as it is, and so do the per-task utility models, which
are chosen on purpose. The effort stays when the new model has it, else it becomes the new model's default.
Conversations that already started keep their own model.
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.model_policy import PolicyTarget, connection_key, model_refusal
from app.core.model_policy_store import resolve_model_policy
from app.core.settings import SETTING_DEFINITIONS, resolve_connection, resolve_setting, store_setting
from app.presets.service import PresetError, list_presets
from app.providers.catalog import ModelInfo, ModelSize
from app.providers.registry import ProviderRegistry
from app.storage.models import ModelPreset

UTILITY_SETTING = "utility_model"
# A preset's role models with their effort fields.
ROLE_FIELDS = (("model", "effort"), ("worker_model", "worker_effort"), ("utility_model", "utility_effort"))
SIZES: frozenset[ModelSize] = frozenset({"large", "medium", "small"})


@dataclass(frozen=True)
class Vendor:
    name: str
    label: str
    # The models a switch picks, largest first, as the switch shows them.
    models: list[str]


@dataclass(frozen=True)
class VendorState:
    vendors: list[Vendor]
    # The vendor of every sized model in the presets, or None when they mix vendors or have none.
    current: str | None


@dataclass(frozen=True)
class Move:
    """One model that a switch replaces: the preset (None for the utility model setting) and its fields."""

    preset: ModelPreset | None
    model_field: str
    effort_field: str
    target: ModelInfo


def vendor_models(providers: ProviderRegistry, vendor: str) -> dict[ModelSize, ModelInfo]:
    """The model a switch to this vendor picks per size: the first of that size in the catalog."""
    picked: dict[ModelSize, ModelInfo] = {}
    for model in providers.models:
        if model.provider == vendor and model.size is not None and model.size not in picked:
            picked[model.size] = model
    return picked


def switchable_vendors(providers: ProviderRegistry) -> list[Vendor]:
    """Vendors with a model of every size, in the order their models are listed."""
    vendors: list[Vendor] = []
    for name in dict.fromkeys(model.provider for model in providers.models):
        picked = vendor_models(providers, name)
        if set(picked) == SIZES:
            order: list[ModelSize] = ["large", "medium", "small"]
            vendors.append(Vendor(name, providers.provider_label(name), [picked[size].display_name for size in order]))
    return vendors


def counterpart(providers: ProviderRegistry, model_id: str | None, vendor: str) -> ModelInfo | None:
    """The vendor's model of the same size, when the model belongs to another vendor and has a size; else None."""
    if not model_id:
        return None
    model = providers.find_model(model_id)
    if model is None or model.size is None or model.provider == vendor:
        return None
    return vendor_models(providers, vendor).get(model.size)


def sized_vendors(providers: ProviderRegistry, presets: list[ModelPreset]) -> set[str]:
    found: set[str] = set()
    for preset in presets:
        for model_field, _effort_field in ROLE_FIELDS:
            model = providers.find_model(getattr(preset, model_field) or "")
            if model is not None and model.size is not None:
                found.add(model.provider)
    return found


def vendor_state(session: Session, providers: ProviderRegistry, environment_id: int) -> VendorState:
    vendors = switchable_vendors(providers)
    found = sized_vendors(providers, list_presets(session, environment_id))
    current = next(iter(found)) if len(found) == 1 else None
    return VendorState(vendors, current if current in {vendor.name for vendor in vendors} else None)


def planned_moves(session: Session, providers: ProviderRegistry, environment_id: int, vendor: str) -> list[Move]:
    moves: list[Move] = []
    for preset in list_presets(session, environment_id):
        for model_field, effort_field in ROLE_FIELDS:
            target = counterpart(providers, getattr(preset, model_field), vendor)
            if target is not None:
                moves.append(Move(preset, model_field, effort_field, target))
    utility = resolve_setting(session, SETTING_DEFINITIONS[UTILITY_SETTING], environment_id).value
    target = counterpart(providers, str(utility or ""), vendor)
    if target is not None:
        moves.append(Move(None, UTILITY_SETTING, "", target))
    return moves


def check_policy(
    session: Session, providers: ProviderRegistry, environment_id: int, vendor: str, moves: list[Move]
) -> None:
    """Refuse the switch when the environment's model policy would refuse one of the new models."""
    spec = providers.find_provider(vendor, resolve_connection(session, vendor, environment_id))
    if spec is None:
        raise PresetError(f"{providers.provider_label(vendor)} has no connection in this installation.")
    policy = resolve_model_policy(session, environment_id)
    for model in {move.target.id: move.target for move in moves}.values():
        target = PolicyTarget(
            model=model.id,
            model_name=model.display_name,
            connection=connection_key(spec),
            connection_name=spec.display_name,
            local=spec.local,
            business_data=model.business_data,
        )
        if (refusal := model_refusal(policy, target)) is not None:
            raise PresetError(refusal)


def apply_move(move: Move) -> None:
    preset = move.preset
    assert preset is not None
    effort = getattr(preset, move.effort_field)
    setattr(preset, move.model_field, move.target.id)
    setattr(preset, move.effort_field, effort if effort in move.target.efforts else move.target.default_effort)
    if move.model_field == "model" and preset.max_output_tokens:
        preset.max_output_tokens = min(preset.max_output_tokens, move.target.max_output_tokens)


def switch_vendor(session: Session, providers: ProviderRegistry, environment_id: int, vendor: str) -> int:
    """Move the environment's presets and utility model to the vendor; returns how many models changed."""
    if vendor not in {item.name for item in switchable_vendors(providers)}:
        raise PresetError("Choose one of the vendors offered.")
    moves = planned_moves(session, providers, environment_id, vendor)
    if moves:
        check_policy(session, providers, environment_id, vendor, moves)
    for move in moves:
        if move.preset is not None:
            apply_move(move)
    setting = next((move for move in moves if move.preset is None), None)
    if setting is not None:
        store_setting(session, SETTING_DEFINITIONS[UTILITY_SETTING], environment_id, setting.target.id)
    session.commit()
    return len(moves)
