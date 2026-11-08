"""Model catalog: the models YAAP offers, their limits and prices.

Prices are per million tokens, as published by the vendor. Anthropic prices were checked on 2026-10-06, OpenAI
prices and models on 2026-11-05 (developers.openai.com/api/docs/pricing and /models); verify them on the vendor's
pricing page when they change.
"""

from dataclasses import dataclass
from typing import Literal

from app.providers.base import TokenUsage

ANTHROPIC_EFFORTS = ["low", "medium", "high", "xhigh", "max"]
# OpenAI's reasoning efforts per model; only Terra and Luna can skip reasoning ("none").
OPENAI_EFFORTS = ["low", "medium", "high", "xhigh", "max"]
OPENAI_EFFORTS_WITH_NONE = ["none", *OPENAI_EFFORTS]
# The longest reply for models without a known maximum.
DEFAULT_MAX_OUTPUT = 64_000
CLAUDE_5_MAX_OUTPUT = 128_000
# A model's place in its vendor's line-up: switching presets to another vendor picks the model of the same size.
ModelSize = Literal["large", "medium", "small"]


@dataclass(frozen=True)
class PriceTier:
    input: float
    output: float
    cache_read: float
    # A cache write kept for five minutes (Anthropic: 1.25x input).
    cache_write: float
    # A cache write kept for an hour (Anthropic: 2x input); None where the vendor offers no such cache.
    cache_write_1h: float | None = None


@dataclass(frozen=True)
class ModelPricing:
    standard: PriceTier
    long_context: PriceTier | None = None
    long_context_threshold: int | None = None

    def tier_for(self, prompt_tokens: int) -> PriceTier:
        if self.long_context and self.long_context_threshold and prompt_tokens > self.long_context_threshold:
            return self.long_context
        return self.standard


@dataclass(frozen=True)
class ModelInfo:
    provider: str
    id: str
    display_name: str
    context_window: int
    efforts: list[str]
    default_effort: str
    pricing: ModelPricing
    server_side_fallback: bool = False
    # The longest reply the model can write, in tokens; a preset can choose less.
    max_output_tokens: int = DEFAULT_MAX_OUTPUT
    # The mark for business data processing: the vendor processes prompts to this model under terms
    # meant for business data. A model policy can require it. Off until it is checked for the model and its terms.
    business_data: bool = False
    # The size within the vendor's line-up; None for models that no preset switch should pick or replace.
    size: ModelSize | None = None


def calculate_cost(model: ModelInfo, usage: TokenUsage) -> float:
    prompt_tokens = usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens
    tier = model.pricing.tier_for(prompt_tokens)
    hour_writes = min(usage.cache_write_1h_tokens, usage.cache_write_tokens)
    hour_price = tier.cache_write_1h if tier.cache_write_1h is not None else tier.cache_write
    total = (
        usage.input_tokens * tier.input
        + usage.output_tokens * tier.output
        + usage.cache_read_tokens * tier.cache_read
        + (usage.cache_write_tokens - hour_writes) * tier.cache_write
        + hour_writes * hour_price
    )
    return round(total / 1_000_000, 6)


ANTHROPIC_MODELS = [
    ModelInfo(
        provider="anthropic",
        id="claude-opus-5-5",
        size="large",
        display_name="Claude Opus 5.5",
        context_window=1_000_000,
        max_output_tokens=CLAUDE_5_MAX_OUTPUT,
        efforts=ANTHROPIC_EFFORTS,
        default_effort="medium",
        # Cache writes: 1.25x input for five minutes, 2x input for an hour.
        pricing=ModelPricing(
            standard=PriceTier(input=4.00, output=20.00, cache_read=0.20, cache_write=5.00, cache_write_1h=8.00)
        ),
        server_side_fallback=True,
    ),
    ModelInfo(
        provider="anthropic",
        id="claude-sonnet-5-5",
        size="medium",
        display_name="Claude Sonnet 5.5",
        context_window=1_000_000,
        max_output_tokens=CLAUDE_5_MAX_OUTPUT,
        efforts=ANTHROPIC_EFFORTS,
        default_effort="high",
        pricing=ModelPricing(
            standard=PriceTier(input=2.00, output=10.00, cache_read=0.20, cache_write=2.50, cache_write_1h=4.00)
        ),
        server_side_fallback=True,
    ),
    ModelInfo(
        provider="anthropic",
        id="claude-haiku-5-5",
        size="small",
        display_name="Claude Haiku 5.5",
        context_window=1_000_000,
        max_output_tokens=CLAUDE_5_MAX_OUTPUT,
        efforts=ANTHROPIC_EFFORTS,
        default_effort="medium",
        # Prompts above 100K tokens are billed at the long-context rate. Cache rates follow the
        # general rule (reads 0.1x, 5-minute writes 1.25x, 1-hour writes 2x input).
        pricing=ModelPricing(
            standard=PriceTier(input=0.10, output=0.50, cache_read=0.01, cache_write=0.125, cache_write_1h=0.20),
            long_context=PriceTier(input=0.50, output=2.50, cache_read=0.05, cache_write=0.625, cache_write_1h=1.00),
            long_context_threshold=100_000,
        ),
        # Haiku has no server-side fallback; a refusal stays a refusal.
        server_side_fallback=False,
    ),
]


def openai_pricing(input_price: float, cached: float, output: float) -> ModelPricing:
    """OpenAI caches prompts automatically with no write charge; prompts above 272K tokens cost double input and
    1.5x output."""
    return ModelPricing(
        standard=PriceTier(input=input_price, output=output, cache_read=cached, cache_write=input_price),
        long_context=PriceTier(input=input_price * 2, output=output * 1.5, cache_read=cached * 2, cache_write=input_price * 2),
        long_context_threshold=272_000,
    )


OPENAI_MODELS = [
    ModelInfo(
        provider="openai",
        id="gpt-6-astra",
        size="large",
        display_name="GPT-6 Astra",
        context_window=1_050_000,
        efforts=OPENAI_EFFORTS,
        default_effort="medium",
        pricing=openai_pricing(10.00, 1.00, 50.00),
    ),
    ModelInfo(
        provider="openai",
        id="gpt-6.1-sol",
        size="medium",
        display_name="GPT-6.1 Sol",
        context_window=1_050_000,
        efforts=OPENAI_EFFORTS,
        default_effort="medium",
        pricing=openai_pricing(2.00, 0.10, 10.00),
    ),
    ModelInfo(
        provider="openai",
        id="gpt-6.1-terra",
        size="medium",
        display_name="GPT-6.1 Terra",
        context_window=1_050_000,
        efforts=OPENAI_EFFORTS_WITH_NONE,
        default_effort="medium",
        pricing=openai_pricing(1.00, 0.05, 5.00),
    ),
    ModelInfo(
        provider="openai",
        id="gpt-6-luna",
        size="small",
        display_name="GPT-6 Luna",
        context_window=1_050_000,
        efforts=OPENAI_EFFORTS_WITH_NONE,
        default_effort="low",
        pricing=openai_pricing(0.10, 0.01, 0.50),
    ),
]
