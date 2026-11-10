"""Ollama: local models through Ollama's OpenAI-compatible Chat Completions API (the shared adapter in
`chat_completions.py`). No API key and no cost. The model list is read from the server (`/api/tags`), with each
model's context window from `/api/show`.

The server address is the global setting `ollama_url`; empty switches Ollama off.
"""

import threading
import time

import httpx

from app.providers.base import ProviderError
from app.providers.catalog import ModelInfo, ModelPricing, PriceTier
from app.providers.chat_completions import MESSAGES_BLOCK_TYPE, ChatCompletionsProvider, to_messages

__all__ = ["MESSAGES_BLOCK_TYPE", "OllamaProvider", "OllamaState", "refresh", "to_messages"]

PROVIDER = "ollama"
MODEL_PREFIX = "ollama/"
DEFAULT_CONTEXT = 32_768
REFRESH_SECONDS = 60
FREE = ModelPricing(PriceTier(0, 0, 0, 0))


class OllamaState:
    """The server address and its models, shared by the registry and the provider."""

    def __init__(self) -> None:
        self.url = ""
        self.models: list[ModelInfo] = []
        self.checked_at = 0.0
        self.error: str | None = None
        self.lock = threading.Lock()


def model_info(name: str, context: int) -> ModelInfo:
    return ModelInfo(
        provider=PROVIDER,
        id=f"{MODEL_PREFIX}{name}",
        display_name=f"{name} (Ollama)",
        context_window=context,
        efforts=["medium"],
        default_effort="medium",
        pricing=FREE,
    )


def read_models(url: str, timeout: float = 3.0) -> list[ModelInfo]:
    """The models the Ollama server has pulled. Raises httpx.HTTPError when it cannot be reached."""
    base = url.rstrip("/")
    with httpx.Client(timeout=timeout) as client:
        tags = client.get(f"{base}/api/tags")
        tags.raise_for_status()
        models = []
        for item in tags.json().get("models", []):
            name = item.get("name") or item.get("model")
            if not name:
                continue
            context = DEFAULT_CONTEXT
            try:
                shown = client.post(f"{base}/api/show", json={"model": name})
                info = shown.json().get("model_info", {}) if shown.status_code == 200 else {}
                lengths = [value for key, value in info.items() if key.endswith(".context_length") and isinstance(value, int)]
                if lengths:
                    context = lengths[0]
            except (httpx.HTTPError, ValueError):
                pass
            models.append(model_info(name, context))
    return sorted(models, key=lambda model: model.id)


def refresh(state: OllamaState, url: str, force: bool = False) -> list[ModelInfo]:
    """The server's models, read again at most every REFRESH_SECONDS (or when the address changed)."""
    with state.lock:
        if not url:
            state.url, state.models, state.error = "", [], None
            return []
        if not force and url == state.url and time.monotonic() - state.checked_at < REFRESH_SECONDS:
            return state.models
        state.url = url
        state.checked_at = time.monotonic()
        try:
            state.models = read_models(url)
            state.error = None
        except (httpx.HTTPError, ValueError) as error:
            # Keep the models known so far: a conversation on a local model still opens while Ollama is down.
            state.error = f"Could not reach Ollama at {url}: {error}"
        return state.models


class OllamaProvider(ChatCompletionsProvider):
    def __init__(self, url: str) -> None:
        if not url:
            raise ProviderError("Ollama is not set up: add its address in Settings.")
        super().__init__(f"{url.rstrip('/')}/v1", "ollama", "Ollama", MODEL_PREFIX)
