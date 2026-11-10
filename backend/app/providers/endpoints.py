"""OpenAI-compatible endpoints (OpenRouter, LM Studio, vLLM and the like): a base URL the user fixes in the global
setting `openai_endpoints`. Each endpoint is a provider of its own, named like the endpoint, with the models
`<name>/<model id>` read from `GET {base_url}/models`.

An endpoint is its own vendor for the model policy and for review by another vendor, whoever runs the model behind it.
Keys are not part of an endpoint yet: the provider spec's `credential_kind` is None, so requests go out without one.
A key per endpoint and environment would be a credential kind per endpoint, sent only to that endpoint's base URL.
"""

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.providers.base import ProviderError
from app.providers.catalog import ModelInfo, ModelPricing, PriceTier
from app.providers.chat_completions import ChatCompletionsProvider
from app.providers.registry import ProviderRegistry, ProviderSpec

CONNECTION = "endpoint"
SETTING_KEY = "openai_endpoints"
DEFAULT_CONTEXT = 32_768
REFRESH_SECONDS = 60
MAX_ENDPOINTS = 20
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")
URL_PATTERN = re.compile(r"^https?://[^\s]+$")
# Providers whose names an endpoint cannot take, besides the ones registered when the setting is checked.
BUILT_IN_NAMES = frozenset({"anthropic", "openai", "ollama"})
# Fields of a model listing that name the context window, in the order they are tried.
CONTEXT_FIELDS = ("context_length", "max_model_len", "context_window")
FREE = ModelPricing(PriceTier(0, 0, 0, 0))
PER_MILLION = 1_000_000


@dataclass(frozen=True)
class Endpoint:
    name: str
    base_url: str
    local: bool = False


def parse_endpoints(value: Any) -> list[Endpoint]:
    """A stored setting value as endpoints. Saving validated it; what does not fit is left out rather than guessed."""
    if not isinstance(value, list):
        return []
    found: list[Endpoint] = []
    for item in value:
        if isinstance(item, dict) and isinstance(item.get("name"), str) and isinstance(item.get("base_url"), str):
            found.append(Endpoint(item["name"], item["base_url"].strip().rstrip("/"), item.get("local") is True))
    return found


def check_endpoints(value: Any, providers: ProviderRegistry) -> str | None:
    """Why the list of endpoints cannot be stored, or None."""
    if not isinstance(value, list) or len(value) > MAX_ENDPOINTS:
        return f"Give a list of at most {MAX_ENDPOINTS} endpoints, each with a name, a base_url and local."
    taken = BUILT_IN_NAMES | {spec.name for spec in providers.specs if spec.connection != CONNECTION}
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) - {"name", "base_url", "local"}:
            return "Each endpoint is an object with a name, a base_url and local."
        name, base_url = item.get("name"), item.get("base_url")
        if not isinstance(name, str) or not NAME_PATTERN.match(name):
            return "An endpoint name uses lowercase letters, digits and hyphens, and is at most 31 characters."
        if name in taken:
            return f"The name {name} is a provider of YAAP already; choose another."
        if name in seen:
            return f"The name {name} is used twice."
        seen.add(name)
        if not isinstance(base_url, str) or not URL_PATTERN.match(base_url.strip()):
            return f"The base URL of {name} is an http(s) address, such as http://localhost:1234/v1."
        if "local" in item and not isinstance(item["local"], bool):
            return f"local of {name} is true or false."
    return None


def positive_number(value: Any) -> float | None:
    """A number in a listing that may be a number or a string; None when it is absent, unreadable or negative."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def listed_pricing(pricing: Any) -> ModelPricing | None:
    """Prices per token (OpenRouter's `pricing`) as prices per million tokens; None when the listing gives none."""
    if not isinstance(pricing, dict):
        return None
    prompt, completion = positive_number(pricing.get("prompt")), positive_number(pricing.get("completion"))
    if prompt is None or completion is None:
        return None
    read = positive_number(pricing.get("input_cache_read"))
    write = positive_number(pricing.get("input_cache_write"))
    return ModelPricing(
        PriceTier(
            input=prompt * PER_MILLION,
            output=completion * PER_MILLION,
            cache_read=(prompt if read is None else read) * PER_MILLION,
            cache_write=(prompt if write is None else write) * PER_MILLION,
        )
    )


def listed_context(item: dict[str, Any]) -> int:
    for key in CONTEXT_FIELDS:
        number = positive_number(item.get(key))
        if number:
            return int(number)
    return DEFAULT_CONTEXT


def parse_models(endpoint: Endpoint, payload: Any) -> list[ModelInfo]:
    """The models of a `/models` listing. Context window and prices come from the listing when it has them; else
    the context is 32k and the model has no price, which makes it unmetered."""
    items = payload.get("data") if isinstance(payload, dict) else None
    models: dict[str, ModelInfo] = {}
    for item in items if isinstance(items, list) else []:
        model_id = item.get("id") if isinstance(item, dict) else None
        if not isinstance(model_id, str) or not model_id:
            continue
        title = item.get("name") if isinstance(item.get("name"), str) and item["name"] else model_id
        top = item.get("top_provider")
        longest = positive_number(top.get("max_completion_tokens")) if isinstance(top, dict) else None
        context = listed_context(item)
        info = ModelInfo(
            provider=endpoint.name,
            id=f"{endpoint.name}/{model_id}",
            display_name=f"{title} ({endpoint.name})",
            context_window=context,
            efforts=["medium"],
            default_effort="medium",
            pricing=listed_pricing(item.get("pricing")) or FREE,
            **({"max_output_tokens": min(int(longest), context)} if longest else {}),
        )
        models[info.id] = info
    return sorted(models.values(), key=lambda model: model.id)


def read_models(endpoint: Endpoint, timeout: float = 5.0) -> list[ModelInfo]:
    """The models an endpoint lists. Raises httpx.HTTPError or ValueError when it cannot be read."""
    with httpx.Client(timeout=timeout) as client:
        response = client.get(f"{endpoint.base_url}/models")
        response.raise_for_status()
        return parse_models(endpoint, response.json())


@dataclass
class EndpointState:
    """The configured endpoints, their models and the last error of each, shared by the registry and the API."""

    endpoints: list[Endpoint] = field(default_factory=list)
    models: list[ModelInfo] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    checked_at: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)


def endpoint_spec(endpoint: Endpoint, priced: bool) -> ProviderSpec:
    def create(_api_key: str | None) -> ChatCompletionsProvider:
        if not endpoint.base_url:
            raise ProviderError(f"The endpoint {endpoint.name} has no base URL.")
        # The SDK wants a key string; the placeholder is not a secret and the server ignores it.
        return ChatCompletionsProvider(endpoint.base_url, "none", endpoint.name, f"{endpoint.name}/")

    return ProviderSpec(
        name=endpoint.name,
        display_name=endpoint.name,
        credential_kind=None,
        create=create,
        connection=CONNECTION,
        runs_agents=True,
        # Budgets apply only where the listing gave prices; a model without a price costs nothing.
        metered=priced,
        local=endpoint.local,
    )


def refresh_endpoints(registry: ProviderRegistry, endpoints: list[Endpoint], force: bool = False) -> None:
    """Make the registry's endpoint providers match the setting and read their models again: at most every
    REFRESH_SECONDS, at once when the endpoints changed or `force` is set."""
    state = registry.endpoints
    if state is None:
        return
    with state.lock:
        changed = endpoints != state.endpoints
        if not (changed or force or time.monotonic() - state.checked_at >= REFRESH_SECONDS):
            return
        known: dict[str, list[ModelInfo]] = {}
        for model in state.models:
            known.setdefault(model.provider, []).append(model)
        old = {endpoint.name: endpoint for endpoint in state.endpoints}
        models: list[ModelInfo] = []
        errors: dict[str, str] = {}
        specs: list[ProviderSpec] = []
        for endpoint in endpoints:
            try:
                found = read_models(endpoint)
            except (httpx.HTTPError, ValueError) as error:
                errors[endpoint.name] = f"Could not read the models of {endpoint.name} at {endpoint.base_url}: {error}"
                # Keep the models known so far, unless the address is another one.
                same = old.get(endpoint.name) is not None and old[endpoint.name].base_url == endpoint.base_url
                found = known.get(endpoint.name, []) if same else []
            models.extend(found)
            specs.append(endpoint_spec(endpoint, priced=any(model.pricing != FREE for model in found)))
        registry.specs[:] = [spec for spec in registry.specs if spec.connection != CONNECTION] + specs
        state.endpoints, state.models, state.errors = list(endpoints), models, errors
        state.checked_at = time.monotonic()
