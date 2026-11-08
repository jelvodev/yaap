import asyncio

from app.credentials.types import test_anthropic_api_key as check_anthropic_api_key


def test_subscription_token_is_refused_without_calling_the_api():
    result = asyncio.run(check_anthropic_api_key("sk-ant-oat01-example-subscription-token"))
    assert result.ok is False
    assert "subscription token" in result.message
    assert "sk-ant-api" in result.message
    assert "Claude connection to Claude Code" in result.message
    assert "docker compose exec -it yaap claude auth login" in result.message
