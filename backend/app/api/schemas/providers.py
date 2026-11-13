"""Provider and model catalog models, including the coding agent CLI status."""

from pydantic import BaseModel


class ModelPricingOut(BaseModel):
    input_per_million: float
    output_per_million: float
    cache_read_per_million: float
    cache_write_per_million: float


class ModelOut(BaseModel):
    provider: str
    id: str
    display_name: str
    context_window: int
    # The longest reply the model can write, in tokens.
    max_output_tokens: int
    efforts: list[str]
    default_effort: str
    pricing: ModelPricingOut
    # The vendor, for grouping in model lists, for example "OpenAI".
    provider_label: str = ""
    # Per environment only: whether the environment can use the model now, and if not, what is missing.
    available: bool = True
    unavailable_reason: str | None = None
    # Per environment only: False when the model policy forbids the model; unavailable_reason then names the policy
    # and its level.
    allowed: bool = True
    # The catalog's mark for business data processing, which a model policy can require.
    business_data: bool = False


class ProviderOut(BaseModel):
    name: str
    connection: str
    display_name: str
    credential_kind: str | None
    # The models run on the user's own computer (Ollama): what a model policy's "only local models" allows.
    local: bool = False


class ClaudeCodeStatusOut(BaseModel):
    installed: bool
    version: str | None
    logged_in: bool
    auth_method: str | None
    subscription_type: str | None
    email: str | None
    sign_in_command: str


class CodexStatusOut(BaseModel):
    installed: bool
    version: str | None
    logged_in: bool
    # Codex's own line about the sign-in, such as "Logged in using ChatGPT".
    detail: str | None
    sign_in_command: str
