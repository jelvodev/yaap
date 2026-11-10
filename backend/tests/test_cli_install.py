"""Installing Claude Code and Codex at their first sign-in: the state decision, the install with a fake npm, the
sign-in that installs first, and the providers' answer when a CLI is missing."""

import asyncio
import json
import re
import sys
from pathlib import Path

import pytest

from app.providers import claude_code_provider, codex_provider
from app.providers.cli_install import (
    INSTALLED_LOCK_FILE,
    LOCK_FILE,
    PACKAGE_FILE,
    CliTools,
    InstallFailed,
    installed_state,
    tool_command,
)
from app.providers.cli_sign_in import CliSignIns
from tests.test_cli_sign_in import FAKE_PASTE, fake_spec, runs_on_event_loop, wait_for

LOCK = b'{"lockfileVersion": 3, "packages": {"": {"name": "yaap-tools"}}}\n'
NEWER_LOCK = b'{"lockfileVersion": 3, "packages": {"": {"name": "yaap-tools", "version": "2"}}}\n'

# Stands in for `npm ci --prefix <folder>`: counts its runs, then creates the CLIs' binaries or fails.
FAKE_NPM = """
import os, sys, time
folder = sys.argv[sys.argv.index("--prefix") + 1]
with open(os.path.join(folder, "runs"), "a") as runs:
    runs.write("run\\n")
time.sleep(DELAY)
if MODE == "fail":
    print("npm error 404 Not Found - registry", file=sys.stderr)
    sys.exit(1)
bin_folder = os.path.join(folder, "node_modules", ".bin")
os.makedirs(bin_folder, exist_ok=True)
for name in ("claude", "codex"):
    with open(os.path.join(bin_folder, name), "w") as binary:
        binary.write("#!/bin/sh\\n")
"""


def fake_tools(tmp_path: Path, mode: str = "ok", delay: float = 0.0, lock: bytes | None = LOCK) -> CliTools:
    shipped = tmp_path / "shipped"
    shipped.mkdir(exist_ok=True)
    if lock is not None:
        (shipped / PACKAGE_FILE).write_text("{}", encoding="utf-8")
        (shipped / LOCK_FILE).write_bytes(lock)
    script = tmp_path / "fake_npm.py"
    script.write_text(FAKE_NPM.replace("MODE", repr(mode)).replace("DELAY", repr(delay)), encoding="utf-8")
    return CliTools(tmp_path / "tools", shipped, npm=(sys.executable, str(script)), timeout=30)


def runs_of(tools: CliTools) -> int:
    runs = tools.folder / "runs"
    return len(runs.read_text().split()) if runs.exists() else 0


@pytest.mark.parametrize(
    ("binary_present", "installed_lock", "shipped_lock", "expected"),
    [
        (True, LOCK, LOCK, "installed"),
        (False, LOCK, LOCK, "not_installed"),
        (True, NEWER_LOCK, LOCK, "not_installed"),
        (True, None, LOCK, "not_installed"),
        (False, None, LOCK, "not_installed"),
        (True, LOCK, None, "not_installed"),
        (True, None, None, "not_installed"),
    ],
)
def test_a_cli_counts_as_installed_only_with_its_binary_and_the_shipped_lock(
    binary_present, installed_lock, shipped_lock, expected
):
    assert installed_state(binary_present, installed_lock, shipped_lock) == expected


def test_the_default_command_is_a_path_in_the_tools_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("YAAP_TOOLS_DIR", str(tmp_path / "tools"))
    assert tool_command("claude") == (str(tmp_path / "tools" / "node_modules" / ".bin" / "claude"),)


@runs_on_event_loop
async def test_installing_makes_both_clis_available_and_records_the_lock(tmp_path):
    tools = fake_tools(tmp_path)
    assert tools.status("claude").state == "not_installed"
    await tools.ensure("claude")
    assert tools.status("claude").state == "installed" and tools.status("codex").state == "installed"
    assert (tools.folder / INSTALLED_LOCK_FILE).read_bytes() == LOCK
    assert (tools.folder / PACKAGE_FILE).exists()
    await tools.ensure("codex")
    assert runs_of(tools) == 1


@runs_on_event_loop
async def test_a_failed_install_reports_why_and_can_be_tried_again(tmp_path):
    tools = fake_tools(tmp_path, mode="fail")
    with pytest.raises(InstallFailed, match="404 Not Found"):
        await tools.ensure("claude")
    failed = tools.status("claude")
    assert failed.state == "failed" and "404 Not Found" in (failed.message or "")
    assert not (tools.folder / INSTALLED_LOCK_FILE).exists()
    fixed = fake_tools(tmp_path, mode="ok")
    await fixed.ensure("claude")
    assert fixed.status("claude").state == "installed"


@runs_on_event_loop
async def test_a_release_without_its_lock_cannot_install(tmp_path):
    tools = fake_tools(tmp_path, lock=None)
    with pytest.raises(InstallFailed, match="tools/package-lock.json"):
        await tools.ensure("claude")
    assert runs_of(tools) == 0


@runs_on_event_loop
async def test_a_missing_npm_is_a_plain_failure(tmp_path):
    tools = fake_tools(tmp_path)
    tools.npm = (str(tmp_path / "no-such-npm"),)
    with pytest.raises(InstallFailed, match="npm is not available"):
        await tools.ensure("claude")


@runs_on_event_loop
async def test_callers_that_arrive_during_an_install_wait_for_the_same_one(tmp_path):
    tools = fake_tools(tmp_path, delay=0.5)
    first = asyncio.create_task(tools.ensure("claude"))
    await asyncio.sleep(0.2)
    assert tools.status("claude").state == "installing"
    await asyncio.gather(first, tools.ensure("codex"), tools.ensure("claude"))
    assert runs_of(tools) == 1
    await tools.stop()


@runs_on_event_loop
async def test_a_caller_that_gives_up_does_not_stop_the_install(tmp_path):
    tools = fake_tools(tmp_path, delay=0.5)
    waiting = asyncio.create_task(tools.ensure("claude"))
    await asyncio.sleep(0.2)
    waiting.cancel()
    await asyncio.gather(waiting, return_exceptions=True)
    assert tools.status("claude").state == "installing"
    await tools.ensure("claude")
    assert runs_of(tools) == 1
    await tools.stop()


@runs_on_event_loop
async def test_a_stopped_install_leaves_nothing_that_counts_as_installed(tmp_path):
    tools = fake_tools(tmp_path, delay=20)
    tools.start()
    await asyncio.sleep(0.5)
    await tools.stop()
    assert tools.status("claude").state != "installed"
    assert not (tools.folder / INSTALLED_LOCK_FILE).exists()


@pytest.mark.parametrize(
    ("binaries", "installed_lock", "installs"),
    [
        (False, None, False),
        (True, LOCK, False),
        (True, NEWER_LOCK, True),
        (True, None, True),
        (False, NEWER_LOCK, False),
    ],
)
@runs_on_event_loop
async def test_at_start_only_clis_of_an_older_release_are_installed_again(tmp_path, binaries, installed_lock, installs):
    tools = fake_tools(tmp_path)
    tools.signed_in_folders = ()
    tools.folder.mkdir(parents=True)
    if binaries:
        (tools.folder / "node_modules" / ".bin").mkdir(parents=True)
        (tools.folder / "node_modules" / ".bin" / "claude").write_text("old")
    if installed_lock is not None:
        (tools.folder / INSTALLED_LOCK_FILE).write_bytes(installed_lock)
    tools.refresh_if_outdated()
    assert tools.installing() is installs
    if tools.task is not None:
        await asyncio.wait({tools.task})
        assert tools.status("claude").state == "installed"
    await tools.stop()


@runs_on_event_loop
async def test_signing_in_installs_the_cli_first(tmp_path):
    tools = fake_tools(tmp_path, delay=0.5)
    service = CliSignIns.of([fake_spec(tmp_path, FAKE_PASTE, tool="claude", signed_in=False)], tools)
    await service.start("claude-code")
    await wait_for(service, "claude-code", "installing")
    assert service.status("claude-code").url is None
    await wait_for(service, "claude-code", "waiting_for_code")
    assert tools.status("claude").state == "installed"
    await service.stop()


@runs_on_event_loop
async def test_an_existing_sign_in_is_kept_when_the_cli_has_to_be_installed(tmp_path):
    tools = fake_tools(tmp_path)
    spec = fake_spec(tmp_path, FAKE_PASTE, tool="claude", signed_in=True)
    service = CliSignIns.of([spec], tools)
    await service.start("claude-code")
    await wait_for(service, "claude-code", "signed_in")
    assert runs_of(tools) == 1
    await service.stop()


@runs_on_event_loop
async def test_signing_in_to_an_installed_cli_does_not_install_again(tmp_path):
    tools = fake_tools(tmp_path)
    await tools.ensure("claude")
    service = CliSignIns.of([fake_spec(tmp_path, FAKE_PASTE, tool="claude", signed_in=False)], tools)
    await service.start("claude-code")
    await wait_for(service, "claude-code", "waiting_for_code")
    assert runs_of(tools) == 1
    await service.stop()


@runs_on_event_loop
async def test_a_failed_install_fails_the_sign_in_with_the_reason(tmp_path):
    tools = fake_tools(tmp_path, mode="fail")
    service = CliSignIns.of([fake_spec(tmp_path, FAKE_PASTE, tool="claude", signed_in=False)], tools)
    await service.start("claude-code")
    await wait_for(service, "claude-code", "failed")
    assert "404 Not Found" in (service.status("claude-code").message or "")
    await service.stop()


@runs_on_event_loop
async def test_cancelling_a_sign_in_while_installing_leaves_the_install_running(tmp_path):
    tools = fake_tools(tmp_path, delay=0.8)
    service = CliSignIns.of([fake_spec(tmp_path, FAKE_PASTE, tool="claude", signed_in=False)], tools)
    await service.start("claude-code")
    await wait_for(service, "claude-code", "installing")
    assert (await service.cancel("claude-code")).state == "cancelled"
    assert tools.installing()
    await service.stop()


@pytest.mark.parametrize(
    ("module", "provider", "name"),
    [
        (claude_code_provider, claude_code_provider.ClaudeCodeProvider, "Claude Code"),
        (codex_provider, codex_provider.CodexProvider, "Codex"),
    ],
)
def test_a_provider_without_its_cli_says_where_to_install_it(module, provider, name):
    assert module.NOT_INSTALLED_MESSAGE.startswith(f"{name} is not installed yet")
    assert "Settings › Connections › Providers" in module.NOT_INSTALLED_MESSAGE
    assert module.DEFAULT_COMMAND == tool_command(module.TOOL)
    assert provider(module.DEFAULT_COMMAND).command == list(module.DEFAULT_COMMAND)


def test_the_image_holds_the_pinned_lock_but_not_the_clis():
    dockerfile = (Path(__file__).parents[2] / "docker" / "Dockerfile").read_text(encoding="utf-8")
    for gone in ("CLI_REFRESH", "CODEX_VERSION", "downloads.claude.ai", "@openai/codex", "claude-code.asc"):
        assert gone not in dockerfile
    assert "COPY tools/package.json tools/package-lock.json" in dockerfile
    assert "YAAP_TOOLS_DIR=/data/tools" in dockerfile and "DISABLE_AUTOUPDATER=1" in dockerfile


def test_the_shipped_lock_pins_both_clis_with_hashes():
    folder = Path(__file__).parents[2] / "tools"
    package = (folder / PACKAGE_FILE).read_text(encoding="utf-8")
    lock = (folder / LOCK_FILE).read_text(encoding="utf-8")
    for name in ("@anthropic-ai/claude-code", "@openai/codex"):
        assert f'"{name}": "' in package and f'"node_modules/{name}"' in lock
    assert "^" not in package and "~" not in package
    assert lock.count('"integrity"') >= 2


@pytest.mark.parametrize(
    ("sign_in_files", "installs"),
    [
        (True, True),
        (False, False),
    ],
)
@runs_on_event_loop
async def test_at_start_clis_an_older_image_carried_are_installed_when_they_were_signed_in(
    tmp_path, sign_in_files, installs
):
    tools = fake_tools(tmp_path)
    sign_in = tmp_path / "claude-code"
    sign_in.mkdir()
    if sign_in_files:
        (sign_in / ".credentials.json").write_text("{}")
    tools.signed_in_folders = (sign_in,)
    tools.refresh_if_outdated()
    assert tools.installing() is installs
    if tools.task is not None:
        await asyncio.wait({tools.task})
        assert tools.status("claude").state == "installed"
    await tools.stop()
