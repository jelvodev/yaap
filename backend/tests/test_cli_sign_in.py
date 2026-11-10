"""Signing in to Claude Code and Codex from the browser: the pure checks, the service with a fake CLI, the API."""

import asyncio
import functools
import re
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.subprocess_env import safe_subprocess_env
from app.providers.claude_code_provider import (
    REFUSED_CODE_PATTERN,
    SIGN_IN_HOSTS as CLAUDE_HOSTS,
    claude_code_sign_in_spec,
)
from app.providers.cli_install import tool_command
from app.providers.cli_sign_in import (
    CliSignIns,
    CodeRefused,
    NoSignIn,
    SignInSpec,
    UnknownCli,
    clean_code,
    find_link,
    find_user_code,
    link_is_allowed,
    strip_ansi,
)
from app.providers.codex_provider import SIGN_IN_HOSTS as CODEX_HOSTS, USER_CODE_PATTERN, codex_sign_in_spec

def runs_on_event_loop(test: Callable[..., Awaitable[None]]) -> Callable[..., None]:
    """The tests of the service are coroutines; the suite has no async plugin."""

    @functools.wraps(test)
    def run(*args: object, **kwargs: object) -> None:
        asyncio.run(test(*args, **kwargs))

    return run


# Prints a link and waits for a code on its input, like `claude auth login`: "good" signs in, "fatal" fails, and any
# other code is refused and asked for again.
FAKE_PASTE = """
import sys
print("Opening browser to sign in...", flush=True)
print("If the browser didn't open, visit: LINK", flush=True)
while True:
    sys.stdout.write("Paste code here if prompted > ")
    sys.stdout.flush()
    code = sys.stdin.readline().strip()
    if code == "good":
        sys.exit(0)
    if code in ("fatal", ""):
        sys.exit(1)
    print("Invalid code. Please make sure the full code was copied.", flush=True)
"""

# Prints a link and a coloured one-time code, then waits by itself, like `codex login --device-auth`.
FAKE_DEVICE = """
import sys, time
print("\\x1b[90m1. Open this link in your browser\\x1b[0m", flush=True)
print("   \\x1b[94mLINK\\x1b[0m", flush=True)
print("2. Enter this one-time code (expires in 15 minutes)", flush=True)
print("   \\x1b[94mA4MS-02JL0\\x1b[0m", flush=True)
time.sleep(float(sys.argv[1]) if len(sys.argv) > 1 else 0.3)
sys.exit(0)
"""

# Prints nothing useful and waits.
FAKE_SILENT = """
import time
time.sleep(60)
"""


def fake_spec(
    tmp_path: Path,
    script: str,
    link: str = "https://claude.com/cai/oauth/authorize?code=true&client_id=x",
    paste_code: bool = True,
    signed_in: bool = True,
    delay: str = "0.3",
    tool: str | None = None,
) -> SignInSpec:
    file = tmp_path / "fake_cli.py"
    file.write_text(script.replace("LINK", link), encoding="utf-8")

    async def reports_signed_in() -> bool:
        return signed_in

    return SignInSpec(
        key="claude-code" if paste_code else "codex",
        display_name="Fake CLI",
        command=(sys.executable, str(file), delay),
        environment=safe_subprocess_env,
        working_directory=lambda: tmp_path,
        allowed_hosts=CLAUDE_HOSTS if paste_code else CODEX_HOSTS,
        paste_code=paste_code,
        user_code_pattern=None if paste_code else USER_CODE_PATTERN,
        signed_in=reports_signed_in,
        refused_code_pattern=REFUSED_CODE_PATTERN if paste_code else None,
        tool=tool,
    )


async def wait_for(service: CliSignIns, key: str, *states: str, seconds: float = 10) -> None:
    async def poll() -> None:
        while service.status(key).state not in states:
            await asyncio.sleep(0.02)

    await asyncio.wait_for(poll(), seconds)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("plain", "plain"),
        ("\x1b[94mhttps://auth.openai.com/codex/device\x1b[0m", "https://auth.openai.com/codex/device"),
        ("\x1b[1;31mred\x1b[0m and \x1b[2Kcleared", "red and cleared"),
        ("\x1b]8;;https://example.com\x07link\x1b]8;;\x07", "link"),
    ],
)
def test_ansi_codes_are_stripped(text, expected):
    assert strip_ansi(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("If the browser didn't open, visit: https://claude.com/cai/oauth/authorize?code=true&a=b", "https://claude.com/cai/oauth/authorize?code=true&a=b"),
        ("1. Open https://auth.openai.com/codex/device.", "https://auth.openai.com/codex/device"),
        ("go to (https://claude.ai/x)", "https://claude.ai/x"),
        ("no link here", None),
    ],
)
def test_the_link_is_found_in_the_output(text, expected):
    assert find_link(text) == expected


@pytest.mark.parametrize(
    ("link", "hosts", "allowed"),
    [
        ("https://claude.com/cai/oauth/authorize?code=true", CLAUDE_HOSTS, True),
        ("https://claude.ai/login", CLAUDE_HOSTS, True),
        ("https://platform.claude.com/oauth/code/callback", CLAUDE_HOSTS, True),
        ("https://CLAUDE.com/x", CLAUDE_HOSTS, True),
        ("https://auth.openai.com/codex/device", CODEX_HOSTS, True),
        ("https://claude.com:443/x", CLAUDE_HOSTS, True),
        ("http://claude.com/x", CLAUDE_HOSTS, False),
        ("https://evil.example/x", CLAUDE_HOSTS, False),
        ("https://claude.com.evil.example/x", CLAUDE_HOSTS, False),
        ("https://evilclaude.com/x", CLAUDE_HOSTS, False),
        ("https://claude.com@evil.example/x", CLAUDE_HOSTS, False),
        ("https://user:pass@claude.com/x", CLAUDE_HOSTS, False),
        ("https://claude.com:8443/x", CLAUDE_HOSTS, False),
        ("https://claude.com:notaport/x", CLAUDE_HOSTS, False),
        ("javascript:alert(1)", CLAUDE_HOSTS, False),
        ("https://claude.com/x", CODEX_HOSTS, False),
        ("https://auth.openai.com/x", CLAUDE_HOSTS, False),
        ("", CLAUDE_HOSTS, False),
    ],
)
def test_only_expected_https_links_are_used(link, hosts, allowed):
    assert link_is_allowed(link, hosts) is allowed


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("abc123", "abc123"),
        ("  abc#def-ghi_jkl  \n", "abc#def-ghi_jkl"),
        ("a" * 512, "a" * 512),
        ("", None),
        ("   \n", None),
        ("a" * 513, None),
        ("abc def", None),
        ("abc\ndef", None),
        ("abc\tdef", None),
        ("abc\x00def", None),
        ("abc\x1bdef", None),
        ("abc def", None),
    ],
)
def test_a_pasted_code_is_cleaned_or_refused(raw, expected):
    if expected is None:
        with pytest.raises(CodeRefused):
            clean_code(raw)
    else:
        assert clean_code(raw) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("   A4MS-02JL0", "A4MS-02JL0"),
        ("2. Enter this one-time code (expires in 15 minutes)", None),
        ("codex login --device-auth", None),
        ("https://auth.openai.com/codex/device", None),
    ],
)
def test_the_one_time_code_is_found_in_the_output(text, expected):
    assert find_user_code(text, USER_CODE_PATTERN) == expected


def test_the_real_specs_run_the_cli_sign_in_commands():
    assert claude_code_sign_in_spec().command == (*tool_command("claude"), "auth", "login")
    assert codex_sign_in_spec().command == (*tool_command("codex"), "login", "--device-auth")
    assert claude_code_sign_in_spec().paste_code and not codex_sign_in_spec().paste_code
    assert re.fullmatch(USER_CODE_PATTERN, "A4MS-02JL0")


@runs_on_event_loop
async def test_pasting_the_code_signs_in(tmp_path):
    service = CliSignIns.of([fake_spec(tmp_path, FAKE_PASTE)])
    assert service.status("claude-code").state == "idle"
    await service.start("claude-code")
    await wait_for(service, "claude-code", "waiting_for_code")
    waiting = service.status("claude-code")
    assert waiting.url == "https://claude.com/cai/oauth/authorize?code=true&client_id=x"
    assert waiting.user_code is None and not waiting.code_sent
    await service.send_code("claude-code", "  good \n")
    assert service.status("claude-code").code_sent
    await wait_for(service, "claude-code", "signed_in")
    await service.stop()


@runs_on_event_loop
async def test_a_refused_code_can_be_pasted_again(tmp_path):
    service = CliSignIns.of([fake_spec(tmp_path, FAKE_PASTE)])
    await service.start("claude-code")
    await wait_for(service, "claude-code", "waiting_for_code")
    await service.send_code("claude-code", "bad")

    async def refused() -> None:
        while service.status("claude-code").code_sent:
            await asyncio.sleep(0.05)

    await asyncio.wait_for(refused(), 10)
    status = service.status("claude-code")
    assert status.state == "waiting_for_code" and "not accepted" in (status.message or "")
    await service.send_code("claude-code", "good")
    assert service.status("claude-code").message is None
    await wait_for(service, "claude-code", "signed_in")
    await service.stop()


@runs_on_event_loop
async def test_a_failing_cli_fails_the_sign_in(tmp_path):
    service = CliSignIns.of([fake_spec(tmp_path, FAKE_PASTE)])
    await service.start("claude-code")
    await wait_for(service, "claude-code", "waiting_for_code")
    await service.send_code("claude-code", "fatal")
    await wait_for(service, "claude-code", "failed")
    assert "Start again" in (service.status("claude-code").message or "")
    await service.stop()


@runs_on_event_loop
async def test_a_refused_code_is_not_written_to_the_process(tmp_path):
    service = CliSignIns.of([fake_spec(tmp_path, FAKE_PASTE)])
    await service.start("claude-code")
    await wait_for(service, "claude-code", "waiting_for_code")
    with pytest.raises(CodeRefused):
        await service.send_code("claude-code", "good\nand more")
    assert not service.status("claude-code").code_sent
    await service.send_code("claude-code", "good")
    await wait_for(service, "claude-code", "signed_in")
    await service.stop()


@runs_on_event_loop
async def test_a_code_cannot_be_sent_twice_or_without_a_sign_in(tmp_path):
    service = CliSignIns.of([fake_spec(tmp_path, FAKE_PASTE)])
    with pytest.raises(NoSignIn):
        await service.send_code("claude-code", "good")
    await service.start("claude-code")
    await wait_for(service, "claude-code", "waiting_for_code")
    await service.send_code("claude-code", "good")
    with pytest.raises(NoSignIn):
        await service.send_code("claude-code", "good")
    await service.stop()


@runs_on_event_loop
async def test_the_device_flow_shows_link_and_code_then_signs_in_by_itself(tmp_path):
    spec = fake_spec(tmp_path, FAKE_DEVICE, link="https://auth.openai.com/codex/device", paste_code=False)
    service = CliSignIns.of([spec])
    await service.start("codex")
    await wait_for(service, "codex", "waiting_for_browser")
    waiting = service.status("codex")
    assert waiting.url == "https://auth.openai.com/codex/device" and waiting.user_code == "A4MS-02JL0"
    with pytest.raises(NoSignIn):
        await service.send_code("codex", "anything")
    await wait_for(service, "codex", "signed_in")
    await service.stop()


@runs_on_event_loop
async def test_finishing_without_a_sign_in_is_a_failure(tmp_path):
    spec = fake_spec(tmp_path, FAKE_DEVICE, link="https://auth.openai.com/codex/device", paste_code=False, signed_in=False)
    service = CliSignIns.of([spec])
    await service.start("codex")
    await wait_for(service, "codex", "failed")
    assert "still not signed in" in (service.status("codex").message or "")
    await service.stop()


@runs_on_event_loop
async def test_a_link_on_another_host_fails_and_is_not_shown(tmp_path):
    service = CliSignIns.of([fake_spec(tmp_path, FAKE_PASTE, link="https://evil.example/phish")])
    await service.start("claude-code")
    await wait_for(service, "claude-code", "failed")
    failed = service.status("claude-code")
    assert failed.url is None and "unexpected site" in (failed.message or "")
    await service.stop()


@runs_on_event_loop
async def test_waiting_too_long_for_the_link_fails(tmp_path):
    service = CliSignIns.of([fake_spec(tmp_path, FAKE_SILENT)])
    service.link_timeout = 0.3
    await service.start("claude-code")
    await wait_for(service, "claude-code", "failed")
    assert "no sign-in link" in (service.status("claude-code").message or "")
    process = service.runs["claude-code"].process
    assert process is not None and process.returncode is not None
    await service.stop()


@runs_on_event_loop
async def test_a_sign_in_that_takes_too_long_is_stopped(tmp_path):
    service = CliSignIns.of([fake_spec(tmp_path, FAKE_PASTE)])
    service.total_timeout = 0.5
    await service.start("claude-code")
    await wait_for(service, "claude-code", "failed")
    assert "took too long" in (service.status("claude-code").message or "")
    process = service.runs["claude-code"].process
    assert process is not None and process.returncode is not None
    await service.stop()


@runs_on_event_loop
async def test_cancelling_kills_the_process(tmp_path):
    service = CliSignIns.of([fake_spec(tmp_path, FAKE_PASTE)])
    await service.start("claude-code")
    await wait_for(service, "claude-code", "waiting_for_code")
    process = service.runs["claude-code"].process
    assert process is not None and process.returncode is None
    assert (await service.cancel("claude-code")).state == "cancelled"
    assert process.returncode is not None
    await service.stop()


@runs_on_event_loop
async def test_starting_again_cancels_the_running_sign_in(tmp_path):
    service = CliSignIns.of([fake_spec(tmp_path, FAKE_PASTE)])
    await service.start("claude-code")
    await wait_for(service, "claude-code", "waiting_for_code")
    first = service.runs["claude-code"]
    await service.start("claude-code")
    assert first.state == "cancelled" and first.process is not None and first.process.returncode is not None
    assert service.runs["claude-code"] is not first
    await wait_for(service, "claude-code", "waiting_for_code")
    await service.send_code("claude-code", "good")
    await wait_for(service, "claude-code", "signed_in")
    await service.stop()


@runs_on_event_loop
async def test_shutdown_ends_a_running_sign_in(tmp_path):
    service = CliSignIns.of([fake_spec(tmp_path, FAKE_PASTE)])
    await service.start("claude-code")
    await wait_for(service, "claude-code", "waiting_for_code")
    process = service.runs["claude-code"].process
    assert process is not None
    await service.stop()
    assert process.returncode is not None and service.status("claude-code").state == "cancelled"


@runs_on_event_loop
async def test_a_cli_that_is_not_installed_fails_the_sign_in(tmp_path):
    spec = fake_spec(tmp_path, FAKE_PASTE)
    missing = SignInSpec(**{**spec.__dict__, "command": (str(tmp_path / "no-such-cli"),)})
    service = CliSignIns.of([missing])
    await service.start("claude-code")
    await wait_for(service, "claude-code", "failed")
    assert "could not be started" in (service.status("claude-code").message or "")
    await service.stop()


@runs_on_event_loop
async def test_an_unknown_cli_is_refused(tmp_path):
    service = CliSignIns.of([fake_spec(tmp_path, FAKE_PASTE)])
    with pytest.raises(UnknownCli):
        service.status("other")


def use_fake_sign_ins(client: TestClient, tmp_path: Path, **options: str | bool) -> None:
    client.app.state.cli_sign_ins = CliSignIns.of([fake_spec(tmp_path, FAKE_PASTE, **options)])


def poll(client: TestClient, path: str, *states: str) -> dict:
    for _ in range(500):
        body = client.get(path).json()
        if body["state"] in states:
            return body
        time.sleep(0.02)
    raise AssertionError(f"never reached {states}: {body}")


def test_the_sign_in_routes_need_a_signed_in_user(client, tmp_path):
    use_fake_sign_ins(client, tmp_path)
    anonymous = TestClient(client.app)
    for method, path in [
        ("GET", "/api/v1/claude-code/sign-in"),
        ("POST", "/api/v1/claude-code/sign-in"),
        ("DELETE", "/api/v1/claude-code/sign-in"),
        ("POST", "/api/v1/claude-code/sign-in/code"),
        ("GET", "/api/v1/codex/sign-in"),
        ("POST", "/api/v1/codex/sign-in"),
        ("DELETE", "/api/v1/codex/sign-in"),
    ]:
        assert anonymous.request(method, path, json={"code": "good"}).status_code == 401, (method, path)


def test_signing_in_through_the_api(client, tmp_path):
    use_fake_sign_ins(client, tmp_path)
    path = "/api/v1/claude-code/sign-in"
    assert client.get(path).json()["state"] == "idle"
    assert client.post(path).status_code == 200
    waiting = poll(client, path, "waiting_for_code")
    assert waiting["url"].startswith("https://claude.com/") and waiting["user_code"] is None
    assert client.post(f"{path}/code", json={"code": "has space"}).status_code == 422
    assert client.post(f"{path}/code", json={"code": "good"}).json()["code_sent"] is True
    assert poll(client, path, "signed_in")["message"] is None


def test_a_sign_in_can_be_cancelled_through_the_api(client, tmp_path):
    use_fake_sign_ins(client, tmp_path)
    path = "/api/v1/claude-code/sign-in"
    client.post(path)
    poll(client, path, "waiting_for_code")
    assert client.delete(path).json()["state"] == "cancelled"
    assert client.post(f"{path}/code", json={"code": "good"}).status_code == 409


def test_a_link_on_another_host_is_a_failure_through_the_api(client, tmp_path):
    use_fake_sign_ins(client, tmp_path, link="https://evil.example/phish")
    path = "/api/v1/claude-code/sign-in"
    client.post(path)
    failed = poll(client, path, "failed")
    assert failed["url"] is None and failed["message"]


def test_a_cli_without_a_sign_in_spec_is_not_found(client, tmp_path):
    use_fake_sign_ins(client, tmp_path)
    assert client.get("/api/v1/codex/sign-in").status_code == 404
    assert client.post("/api/v1/codex/sign-in").status_code == 404
    assert client.delete("/api/v1/codex/sign-in").status_code == 404
