"""One environment for every subprocess: YAAP's own variables and credentials never reach one by inheritance."""

import subprocess
from pathlib import Path
from typing import Any

import pytest

from app.agent.command_tools import command_environment
from app.checkpoints import shadow
from app.checkpoints.shadow import ShadowRepository
from app.core.subprocess_env import safe_subprocess_env
from app.git import service as git_service
from app.providers.claude_code_provider import claude_code_environment
from app.providers.codex_provider import TOKEN_VARIABLE, codex_environment

MASTER_KEY = "master-key-must-not-leak"


@pytest.mark.parametrize(
    ("name", "only", "kept"),
    [
        ("YAAP_MASTER_KEY", None, False),
        ("YAAP_MASTER_KEY_FILE", None, False),
        ("YAAP_MASTER_KEY_PREVIOUS", None, False),
        ("YAAP_SANDBOX_TOKEN_FILE", None, False),
        ("yaap_master_key", None, False),
        ("ANTHROPIC_API_KEY", None, False),
        ("ANTHROPIC_AUTH_TOKEN", None, False),
        ("CLAUDE_CODE_OAUTH_TOKEN", None, False),
        ("OPENAI_API_KEY", None, False),
        ("CODEX_API_KEY", None, False),
        ("GITHUB_TOKEN", None, False),
        ("GH_TOKEN", None, False),
        ("PATH", None, True),
        ("JAVA_HOME", None, True),
        ("PLAYWRIGHT_BROWSERS_PATH", None, True),
        ("GIT_AUTHOR_NAME", None, True),
        # A provider CLI: the basics and what it names, nothing else.
        ("PATH", {"CLAUDE_CONFIG_DIR"}, True),
        ("HOME", {"CLAUDE_CONFIG_DIR"}, True),
        ("SYSTEMROOT", {"CLAUDE_CONFIG_DIR"}, True),
        ("https_proxy", {"CLAUDE_CONFIG_DIR"}, True),
        ("LC_ALL", {"CLAUDE_CONFIG_DIR"}, True),
        ("CLAUDE_CONFIG_DIR", {"CLAUDE_CONFIG_DIR"}, True),
        ("JAVA_HOME", {"CLAUDE_CONFIG_DIR"}, False),
        ("GIT_AUTHOR_NAME", {"CLAUDE_CONFIG_DIR"}, False),
        ("YAAP_MASTER_KEY", {"YAAP_MASTER_KEY"}, False),
        ("ANTHROPIC_API_KEY", {"ANTHROPIC_API_KEY"}, False),
    ],
)
def test_what_a_subprocess_inherits(name: str, only: set[str] | None, kept: bool) -> None:
    environment = safe_subprocess_env(only=only, source={name: "value"})
    assert (name in environment) is kept


@pytest.mark.parametrize(
    "extra",
    [
        {"GITHUB_TOKEN": "configured for this MCP server"},
        {"YAAP_TOOL_TOKEN": "per run"},
        {"PATH": "/explicit"},
    ],
)
def test_what_the_caller_passes_on_purpose_wins(extra: dict[str, str]) -> None:
    environment = safe_subprocess_env(extra, only=set(), source={"PATH": "/usr/bin", "GITHUB_TOKEN": "from the server"})
    assert environment.items() >= extra.items()


@pytest.fixture
def secrets_in_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YAAP_MASTER_KEY", MASTER_KEY)
    monkeypatch.setenv("YAAP_MASTER_KEY_PREVIOUS", MASTER_KEY)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "provider-key")
    monkeypatch.setenv("UNRELATED_TOOL_HOME", "/opt/tool")


def leaked(environment: dict[str, str]) -> list[str]:
    return sorted(name for name, value in environment.items() if value in (MASTER_KEY, "provider-key"))


@pytest.mark.usefixtures("secrets_in_environment")
def test_commands_keep_the_user_toolchain_but_no_secrets() -> None:
    environment = command_environment()
    assert leaked(environment) == []
    assert environment["UNRELATED_TOOL_HOME"] == "/opt/tool"


@pytest.mark.usefixtures("secrets_in_environment")
def test_provider_clis_get_only_what_they_need(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    claude = claude_code_environment(4096)
    codex = codex_environment("run-token")
    for environment in (claude, codex):
        assert leaked(environment) == [] and "UNRELATED_TOOL_HOME" not in environment
    assert claude["CLAUDE_CONFIG_DIR"] == str(tmp_path / "claude") and claude["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "4096"
    assert codex["CODEX_HOME"] == str(tmp_path / "codex") and codex[TOKEN_VARIABLE] == "run-token"


class CapturedRun:
    """Stands in for subprocess.run and keeps the environment each call was given."""

    def __init__(self) -> None:
        self.environments: list[dict[str, str]] = []

    def __call__(self, command: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        self.environments.append(dict(options["env"]))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


@pytest.mark.usefixtures("secrets_in_environment")
def test_git_gets_no_master_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured = CapturedRun()
    monkeypatch.setattr(git_service.subprocess, "run", captured)
    git_service.run_git(["status"], tmp_path, extra_environment={"GIT_ASKPASS": "/helper"})
    [environment] = captured.environments
    assert leaked(environment) == []
    assert environment["GIT_ASKPASS"] == "/helper" and environment["GIT_TERMINAL_PROMPT"] == "0"


@pytest.mark.usefixtures("secrets_in_environment")
def test_checkpoints_get_no_master_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured = CapturedRun()
    monkeypatch.setattr(shadow.subprocess, "run", captured)
    ShadowRepository(tmp_path / "shadow.git", tmp_path).git("status")
    [environment] = captured.environments
    assert leaked(environment) == []
    assert environment["GIT_DIR"] == str(tmp_path / "shadow.git")


@pytest.mark.usefixtures("secrets_in_environment")
def test_a_real_git_process_cannot_see_the_master_key(tmp_path: Path) -> None:
    # A git alias that runs a shell prints what git's children see.
    result = git_service.run_git(["printenv-key"], tmp_path, config=["alias.printenv-key=!echo \"[${YAAP_MASTER_KEY}]\""])
    assert result.stdout.strip() == "[]"
