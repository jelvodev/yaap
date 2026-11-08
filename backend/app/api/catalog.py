from fastapi import APIRouter

from app.api.dependencies import EnvironmentDependency, ProviderRegistryDependency, SessionDependency
from app.core.settings import resolve_connection
from app.credentials.store import find_credential
from app.providers.catalog import ModelInfo
from app.providers.registry import ProviderRegistry
from app.api.schemas import ClaudeCodeStatusOut, CodexStatusOut, ModelOut, ModelPricingOut, ProviderOut
from app.providers.claude_code_provider import read_claude_code_status
from app.providers.codex_provider import read_codex_status

router = APIRouter(tags=["catalog"])


@router.get("/claude-code/status", response_model=ClaudeCodeStatusOut)
async def claude_code_status() -> ClaudeCodeStatusOut:
    """Whether Claude Code is installed in this container and signed in. One sign-in serves all environments."""
    status = await read_claude_code_status()
    return ClaudeCodeStatusOut(
        installed=status.installed,
        version=status.version,
        logged_in=status.logged_in,
        auth_method=status.auth_method,
        subscription_type=status.subscription_type,
        email=status.email,
        sign_in_command=status.sign_in_command,
    )


@router.get("/codex/status", response_model=CodexStatusOut)
async def codex_status() -> CodexStatusOut:
    """Whether Codex is installed in this container and signed in. One sign-in serves all environments."""
    status = await read_codex_status()
    return CodexStatusOut(
        installed=status.installed,
        version=status.version,
        logged_in=status.logged_in,
        detail=status.detail,
        sign_in_command=status.sign_in_command,
    )


@router.get("/providers", response_model=list[ProviderOut])
def list_providers(providers: ProviderRegistryDependency) -> list[ProviderOut]:
    return [
        ProviderOut(
            name=spec.name,
            connection=spec.connection,
            display_name=spec.display_name,
            credential_kind=spec.credential_kind,
            local=spec.local,
        )
        for spec in providers.specs
    ]


def model_to_out(model: ModelInfo, providers: ProviderRegistry) -> ModelOut:
    return ModelOut(
        provider=model.provider,
        provider_label=providers.provider_label(model.provider),
        id=model.id,
        display_name=model.display_name,
        context_window=model.context_window,
        max_output_tokens=model.max_output_tokens,
        efforts=model.efforts,
        default_effort=model.default_effort,
        business_data=model.business_data,
        pricing=ModelPricingOut(
            input_per_million=model.pricing.standard.input,
            output_per_million=model.pricing.standard.output,
            cache_read_per_million=model.pricing.standard.cache_read,
            cache_write_per_million=model.pricing.standard.cache_write,
        ),
    )


def refresh_local_models(session: SessionDependency, providers: ProviderRegistry) -> None:
    """Read Ollama's models again when its address is set (at most every minute)."""
    if providers.ollama is None:
        return
    from app.core.settings import SETTING_DEFINITIONS, resolve_setting
    from app.providers.ollama_provider import refresh

    refresh(providers.ollama, str(resolve_setting(session, SETTING_DEFINITIONS["ollama_url"], None).value or ""))


@router.get("/models", response_model=list[ModelOut])
def list_models(providers: ProviderRegistryDependency, session: SessionDependency) -> list[ModelOut]:
    refresh_local_models(session, providers)
    return [model_to_out(model, providers) for model in providers.models]


@router.get("/environments/{environment_id}/models", response_model=list[ModelOut])
def list_environment_models(
    environment: EnvironmentDependency, session: SessionDependency, providers: ProviderRegistryDependency
) -> list[ModelOut]:
    """Every model, marked with whether this environment has a working connection for it."""
    refresh_local_models(session, providers)
    result = []
    for model in providers.models:
        out = model_to_out(model, providers)
        spec = providers.find_provider(model.provider, resolve_connection(session, model.provider, environment.id))
        if spec is None:
            out.available, out.unavailable_reason = False, "This connection is not available."
        elif spec.credential_kind is not None and find_credential(session, environment.id, spec.credential_kind) is None:
            out.available = False
            out.unavailable_reason = f"Add an {spec.display_name} API key in Settings to use this model."
        result.append(out)
    return result
