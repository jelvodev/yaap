"""The OpenAI-compatible endpoints as set in Settings: their models and whether they could be read."""

from pydantic import BaseModel

from fastapi import APIRouter

from app.api.dependencies import ProviderRegistryDependency, SessionDependency
from app.core.endpoint_sync import sync_endpoints
from app.providers.registry import ProviderRegistry

router = APIRouter(prefix="/openai-endpoints", tags=["catalog"])


class EndpointModelOut(BaseModel):
    id: str
    display_name: str
    context_window: int
    # Whether the listing gave prices; a model without them is not metered.
    priced: bool


class EndpointOut(BaseModel):
    name: str
    base_url: str
    local: bool
    models: list[EndpointModelOut]
    # Why the models could not be read the last time, or None.
    error: str | None


def endpoint_statuses(providers: ProviderRegistry) -> list[EndpointOut]:
    state = providers.endpoints
    if state is None:
        return []
    return [
        EndpointOut(
            name=endpoint.name,
            base_url=endpoint.base_url,
            local=endpoint.local,
            models=[
                EndpointModelOut(
                    id=model.id,
                    display_name=model.display_name,
                    context_window=model.context_window,
                    priced=model.pricing.standard.input > 0 or model.pricing.standard.output > 0,
                )
                for model in state.models
                if model.provider == endpoint.name
            ],
            error=state.errors.get(endpoint.name),
        )
        for endpoint in state.endpoints
    ]


@router.get("", response_model=list[EndpointOut])
def list_endpoints(providers: ProviderRegistryDependency, session: SessionDependency) -> list[EndpointOut]:
    sync_endpoints(session, providers)
    return endpoint_statuses(providers)


@router.post("/refresh", response_model=list[EndpointOut])
def refresh_all_endpoints(providers: ProviderRegistryDependency, session: SessionDependency) -> list[EndpointOut]:
    """Read the models of every endpoint again now."""
    sync_endpoints(session, providers, force=True)
    return endpoint_statuses(providers)
