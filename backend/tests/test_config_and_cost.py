import pytest

from app.config import ConfigurationError, load_settings
from app.providers.base import TokenUsage
from app.providers.catalog import ANTHROPIC_MODELS, calculate_cost


def test_missing_master_key_stops_startup(monkeypatch):
    monkeypatch.delenv("YAAP_MASTER_KEY", raising=False)
    with pytest.raises(ConfigurationError, match="Fernet.generate_key"):
        load_settings()


def test_invalid_master_key_stops_startup(monkeypatch):
    monkeypatch.setenv("YAAP_MASTER_KEY", "not-a-key")
    with pytest.raises(ConfigurationError):
        load_settings()


def model(model_id: str):
    return next(entry for entry in ANTHROPIC_MODELS if entry.id == model_id)


def test_cost_uses_all_token_kinds():
    usage = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000, cache_read_tokens=1_000_000, cache_write_tokens=0)
    assert calculate_cost(model("claude-opus-5-5"), usage) == pytest.approx(4.00 + 20.00 + 0.20)


def test_long_context_tier_applies_above_threshold():
    short = TokenUsage(input_tokens=100_000, output_tokens=0)
    long = TokenUsage(input_tokens=100_001, output_tokens=0)
    haiku = model("claude-haiku-5-5")
    assert calculate_cost(haiku, short) == pytest.approx(0.01)
    # Costs are rounded to millionths of a dollar.
    assert calculate_cost(haiku, long) == pytest.approx(100_001 * 0.50 / 1_000_000, abs=1e-6)
