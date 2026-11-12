"""Registry of model providers, the ways YAAP connects to them, and the models they offer."""

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from app.providers.base import ModelProvider
from app.providers.catalog import ModelInfo
from app.providers.cli_sign_in import SignInSpec

if TYPE_CHECKING:
    from app.providers.endpoints import EndpointState


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    display_name: str
    # The credential this connection needs per environment, or None when it needs none (Claude Code signs in itself).
    credential_kind: str | None
    create: Callable[[str | None], ModelProvider]
    # How YAAP reaches the provider's models. One provider can have several connections; a setting picks one.
    connection: str = "api_key"
    # Whether this connection runs project conversations as agents with YAAP's tools.
    runs_agents: bool = False
    # Whether each call is charged (an API key), so budgets apply; a subscription is not.
    metered: bool = True
    # Whether the models run on a computer of the user's own (Ollama), so no prompt leaves for a vendor. The model
    # policy's "only local models" allows exactly these.
    local: bool = False
    # Whether the connection can keep the cacheable system parts for an hour (AgentRequest.cache_ttl): the Anthropic
    # API key can; Claude Code and Codex manage their own cache, OpenAI caches by itself.
    long_cache: bool = False
    # How long the connection's prompt cache lives after the last call; None where nothing cached is lost by a pause
    # (local models, endpoints, plugins).
    cache_lifetime: timedelta | None = None
    # For a connection that signs in by itself (a subscription CLI): whether it reports a sign-in now. It starts the
    # CLI, so callers do not run it on every request.
    signed_in: Callable[[], Awaitable[bool]] | None = None
    # How a subscription CLI is signed in from the browser, when it can be.
    sign_in: SignInSpec | None = None


class ProviderRegistry:
    def __init__(
        self,
        providers: Iterable[ProviderSpec],
        models: Iterable[ModelInfo],
        ollama: Any = None,
        endpoints: "EndpointState | None" = None,
    ) -> None:
        self.specs = list(providers)
        names = {spec.name for spec in self.specs}
        self.fixed_models = [model for model in models if model.provider in names]
        # Local models: read from the Ollama server, so the list changes while YAAP runs.
        self.ollama = ollama
        # OpenAI-compatible endpoints: their providers and models change with the global setting.
        self.endpoints = endpoints

    @property
    def models(self) -> list[ModelInfo]:
        local = self.ollama.models if self.ollama is not None else []
        listed = self.endpoints.models if self.endpoints is not None else []
        return [*self.fixed_models, *local, *listed]

    def find_provider(self, name: str, connection: str | None = None) -> ProviderSpec | None:
        """The spec for a provider and connection; without a connection, the first one registered."""
        return next(
            (spec for spec in self.specs if spec.name == name and connection in (None, spec.connection)), None
        )

    def provider_label(self, name: str) -> str:
        """The vendor's name, for grouping: Anthropic for both of its connections."""
        spec = self.find_provider(name)
        return spec.display_name if spec is not None else name

    def find_model(self, model_id: str) -> ModelInfo | None:
        return next((model for model in self.models if model.id == model_id), None)

    def add(self, spec: ProviderSpec, models: Iterable[ModelInfo]) -> None:
        """A provider from a plugin, with its models; the plugin host checks for clashes first."""
        self.specs.append(spec)
        self.fixed_models.extend(model for model in models if model.provider == spec.name)

    def remove(self, spec: ProviderSpec) -> None:
        self.specs.remove(spec)
        if not any(other.name == spec.name for other in self.specs):
            self.fixed_models = [model for model in self.fixed_models if model.provider != spec.name]


def default_provider_registry() -> ProviderRegistry:
    from app.providers.anthropic_provider import AnthropicProvider
    from app.providers.catalog import ANTHROPIC_MODELS, OPENAI_MODELS
    from app.providers.claude_code_provider import (
        ClaudeCodeProvider,
        claude_code_sign_in_spec,
        claude_code_signed_in,
    )
    from app.providers.codex_provider import CodexProvider, codex_sign_in_spec, codex_signed_in
    from app.providers.openai_provider import OpenAIProvider

    fallback_models = frozenset(model.id for model in ANTHROPIC_MODELS if model.server_side_fallback)

    def create_api_provider(api_key: str | None) -> ModelProvider:
        assert api_key is not None
        return AnthropicProvider(api_key, fallback_models)

    anthropic_api = ProviderSpec(
        name="anthropic",
        display_name="Anthropic",
        credential_kind="anthropic_api_key",
        create=create_api_provider,
        connection="api_key",
        runs_agents=True,
        long_cache=True,
        # The hour applies when the cache policy chose it; return_notice handles that.
        cache_lifetime=timedelta(minutes=5),
    )
    claude_code = ProviderSpec(
        name="anthropic",
        display_name="Claude Code",
        credential_kind=None,
        create=lambda _api_key: ClaudeCodeProvider(),
        connection="claude_code",
        runs_agents=True,
        metered=False,
        # A safe guess: the CLI manages the cache itself.
        cache_lifetime=timedelta(hours=1),
        signed_in=claude_code_signed_in,
        sign_in=claude_code_sign_in_spec(),
    )
    def create_openai_provider(api_key: str | None) -> ModelProvider:
        assert api_key is not None
        return OpenAIProvider(api_key)

    openai_api = ProviderSpec(
        name="openai",
        display_name="OpenAI",
        credential_kind="openai_api_key",
        create=create_openai_provider,
        connection="api_key",
        runs_agents=True,
        # OpenAI caches by itself for a few minutes.
        cache_lifetime=timedelta(minutes=5),
    )
    codex = ProviderSpec(
        name="openai",
        display_name="Codex",
        credential_kind=None,
        create=lambda _api_key: CodexProvider(),
        connection="codex",
        runs_agents=True,
        metered=False,
        # A safe guess: the CLI manages the cache itself.
        cache_lifetime=timedelta(hours=1),
        signed_in=codex_signed_in,
        sign_in=codex_sign_in_spec(),
    )
    from app.providers.endpoints import EndpointState
    from app.providers.ollama_provider import OllamaProvider, OllamaState

    ollama_state = OllamaState()
    ollama = ProviderSpec(
        name="ollama",
        display_name="Ollama",
        credential_kind=None,
        create=lambda _api_key: OllamaProvider(ollama_state.url),
        connection="local",
        runs_agents=True,
        metered=False,
        local=True,
    )
    return ProviderRegistry(
        [anthropic_api, claude_code, openai_api, codex, ollama],
        [*ANTHROPIC_MODELS, *OPENAI_MODELS],
        ollama_state,
        EndpointState(),
    )
