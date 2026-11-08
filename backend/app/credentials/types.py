"""Credential types: what can be stored, and how a value is tested before it is saved."""

import asyncio
import json
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import anthropic
import openai

from app.providers.claude_code_provider import SIGN_IN_COMMAND
from app.providers.codex_provider import SIGN_IN_COMMAND as CODEX_SIGN_IN_COMMAND


@dataclass(frozen=True)
class CredentialTestResult:
    ok: bool
    message: str


CredentialTester = Callable[[str], Awaitable[CredentialTestResult]]
CredentialChecker = Callable[[str], str | None]


def no_format_problem(_value: str) -> str | None:
    return None


@dataclass(frozen=True)
class CredentialType:
    kind: str
    label: str
    description: str
    test: CredentialTester
    # Offline check of the value's format. Also runs on a stored value before it is used, so a value that was
    # saved before a check existed is still caught. Returns a readable problem, or None when the format is fine.
    find_format_problem: CredentialChecker = no_format_problem


SUBSCRIPTION_TOKEN_PREFIX = "sk-ant-oat"


def find_anthropic_key_problem(value: str) -> str | None:
    if value.startswith(SUBSCRIPTION_TOKEN_PREFIX):
        # Tokens from a Claude subscription (for example `claude setup-token` on a Max plan) are meant for
        # Anthropic's own apps such as Claude Code; the API refuses them for YAAP's requests.
        return (
            "This is a Claude subscription token (from Claude Code or a Pro/Max plan), not an API key. "
            "To use your subscription, set Claude connection to Claude Code and sign in once from the YAAP "
            f"folder with: {SIGN_IN_COMMAND}. To pay per use instead, create an API key in the Anthropic "
            "Console; it starts with sk-ant-api."
        )
    return None


async def test_anthropic_api_key(value: str) -> CredentialTestResult:
    """Check the key with a read-only call that costs no tokens."""
    problem = find_anthropic_key_problem(value)
    if problem is not None:
        return CredentialTestResult(False, problem)
    client = anthropic.AsyncAnthropic(api_key=value, max_retries=1, timeout=20.0)
    try:
        await client.models.list(limit=1)
    except anthropic.AuthenticationError:
        return CredentialTestResult(False, "Anthropic rejected this key.")
    except anthropic.PermissionDeniedError:
        return CredentialTestResult(False, "This key has no permission to use the API.")
    except anthropic.APIConnectionError:
        return CredentialTestResult(False, "Could not reach Anthropic. Check the network connection.")
    except anthropic.APIStatusError as error:
        return CredentialTestResult(False, f"Anthropic returned an error (HTTP {error.status_code}).")
    finally:
        await client.close()
    return CredentialTestResult(True, "The key works.")


ANTHROPIC_API_KEY = CredentialType(
    kind="anthropic_api_key",
    label="Anthropic API key",
    description=(
        "Used for Claude models. Create one in the Anthropic Console (it starts with sk-ant-api); "
        "a Claude Pro/Max subscription is used through the Claude Code connection instead."
    ),
    test=test_anthropic_api_key,
    find_format_problem=find_anthropic_key_problem,
)


async def test_openai_api_key(value: str) -> CredentialTestResult:
    """Check the key by listing models: a read-only call that costs no tokens."""
    client = openai.AsyncOpenAI(api_key=value, max_retries=1, timeout=20.0)
    try:
        await client.models.list()
    except openai.AuthenticationError:
        return CredentialTestResult(False, "OpenAI rejected this key.")
    except openai.PermissionDeniedError:
        return CredentialTestResult(False, "This key has no permission to list models.")
    except openai.APIConnectionError:
        return CredentialTestResult(False, "Could not reach OpenAI. Check the network connection.")
    except openai.APIStatusError as error:
        return CredentialTestResult(False, f"OpenAI returned an error (HTTP {error.status_code}).")
    finally:
        await client.close()
    return CredentialTestResult(True, "The key works.")


OPENAI_API_KEY = CredentialType(
    kind="openai_api_key",
    label="OpenAI API key",
    description=(
        "Used for GPT models. Create one on the OpenAI platform under API keys; calls are charged per use to the "
        "key's project. A ChatGPT subscription is used through the Codex connection instead; sign in once with: "
        f"{CODEX_SIGN_IN_COMMAND}."
    ),
    test=test_openai_api_key,
)


GITHUB_API_USER = "https://api.github.com/user"


def request_github_user(token: str) -> tuple[int, str]:
    request = urllib.request.Request(
        GITHUB_API_USER,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "YAAP",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, ""


async def test_github_token(value: str) -> CredentialTestResult:
    """Read the token's own user: a read-only call that works for fine-grained and classic tokens."""
    try:
        status, body = await asyncio.to_thread(request_github_user, value)
    except (urllib.error.URLError, TimeoutError):
        return CredentialTestResult(False, "Could not reach GitHub. Check the network connection.")
    if status == 401:
        return CredentialTestResult(False, "GitHub rejected this token.")
    if status != 200:
        return CredentialTestResult(False, f"GitHub returned an error (HTTP {status}).")
    login = json.loads(body).get("login", "")
    return CredentialTestResult(True, f"The token works for {login}." if login else "The token works.")


GITHUB_TOKEN = CredentialType(
    kind="github_token",
    label="GitHub token",
    description=(
        "Used to clone, pull and push this environment's repositories. Create a fine-grained personal access "
        "token limited to the repositories YAAP may use, with Contents read and write."
    ),
    test=test_github_token,
)


def default_credential_types() -> dict[str, CredentialType]:
    return {
        credential_type.kind: credential_type
        for credential_type in (ANTHROPIC_API_KEY, OPENAI_API_KEY, GITHUB_TOKEN)
    }
