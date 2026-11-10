from fastapi import APIRouter, HTTPException, status

from app.api.dependencies import EnvironmentDependency, ProviderRegistryDependency, SessionDependency
from app.core.endpoint_sync import sync_endpoints
from app.core.model_listing import model_availability
from app.providers.catalog import ModelInfo
from app.providers.registry import ProviderRegistry
from app.api.schemas import ClaudeCodeStatusOut, CodexStatusOut, ModelOut, ModelPricingOut, ProviderOut
from app.providers.claude_code_provider import read_claude_code_status
from app.providers.codex_provider import read_codex_status
from app.storage.models import Project

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
    """Read the models of Ollama and of the OpenAI-compatible endpoints again (at most every minute)."""
    sync_endpoints(session, providers)
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
    environment: EnvironmentDependency,
    session: SessionDependency,
    providers: ProviderRegistryDependency,
    project_id: int | None = None,
) -> list[ModelOut]:
    """Every model, marked with whether this environment has a working connection for it and whether the model
    policy allows it. With project_id, the policy of that project of the environment applies as well."""
    if project_id is not None:
        project = session.get(Project, project_id)
        if project is None or project.environment_id != environment.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found in this environment.")
    refresh_local_models(session, providers)
    result = []
    for model in providers.models:
        state = model_availability(session, providers, environment.id, model, project_id)
        out = model_to_out(model, providers)
        out.available, out.allowed, out.unavailable_reason = state.available, state.allowed, state.reason
        result.append(out)
    return result
