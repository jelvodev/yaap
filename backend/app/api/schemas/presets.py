"""Preset models: model and effort bundles, their routing, and switching all presets to a vendor."""

from pydantic import BaseModel, Field

from app.core.routing import RouteKind, RouteRole
from app.presets.delegation import DelegationMode


class PresetRouteOut(BaseModel):
    """Which of the preset's roles answers one kind of message when the preset routes."""

    kind: RouteKind
    label: str
    role: RouteRole


class PresetOut(BaseModel):
    id: int
    environment_id: int
    name: str
    model: str
    effort: str
    is_default: bool
    # Models by role; empty means the fallback.
    worker_model: str | None = None
    worker_effort: str | None = None
    utility_model: str | None = None
    utility_effort: str | None = None
    # The longest reply in tokens; null is the model's maximum.
    max_output_tokens: int | None = None
    # Conversations outside a project start with this preset (the plain_chat_preset setting).
    plain_chats: bool = False
    # Routing: per reply the utility model names the kind of message, and each kind goes to a role.
    routing: bool = False
    routes: list[PresetRouteOut] = Field(default_factory=list)
    delegation: DelegationMode = "free"


class VendorOut(BaseModel):
    """A vendor the presets can switch to, with the models a switch picks, largest first."""

    name: str
    label: str
    models: list[str]


class PresetVendorOut(BaseModel):
    vendors: list[VendorOut]
    # The vendor all presets use; null when they mix vendors.
    current: str | None = None


class PresetVendorSwitch(BaseModel):
    vendor: str = Field(min_length=1, max_length=40)


class PresetVendorSwitched(PresetVendorOut):
    # How many models moved, in the presets and the environment's utility model.
    changed: int


class PresetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    model: str
    effort: str
    worker_model: str | None = None
    worker_effort: str | None = None
    utility_model: str | None = None
    utility_effort: str | None = None
    # The longest reply in tokens; null is the model's maximum.
    max_output_tokens: int | None = None
    # Left out, a new preset delegates freely.
    delegation: DelegationMode | None = None


class PresetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    model: str | None = None
    effort: str | None = None
    # Only true is meaningful: making another preset the default unsets this one.
    is_default: bool | None = None
    # Send a role's model as null or "" to clear it.
    worker_model: str | None = None
    worker_effort: str | None = None
    utility_model: str | None = None
    utility_effort: str | None = None
    # Send null to return to the model's maximum.
    max_output_tokens: int | None = None
    # Turn routing on or off; the kinds sent change their role, the others keep theirs.
    routing: bool | None = None
    routes: dict[RouteKind, RouteRole] | None = None
    delegation: DelegationMode | None = None
