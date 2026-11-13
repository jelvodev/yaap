"""Web fetches run where the run's commands run, refuse when that place is offline, and carry no shell input."""

import asyncio
import base64
import json
import shlex
import subprocess
import sys
from types import SimpleNamespace

import pytest

from app.agent.toolkit import ToolError
from app.agent.web_tools import fetch_for
from app.targets.base import CapturedResult
from app.websearch.runner_fetch import BOOTSTRAP, fetch_command, fetch_place, fetch_with
from app.websearch.safe_fetch import FetchError


@pytest.mark.parametrize(
    ("kind", "online", "place"),
    [
        (None, True, "process"),
        (None, False, "process"),
        ("container", True, "process"),
        ("container", False, "process"),
        ("runner", True, "runner"),
        ("runner", False, "refused"),
        ("sandbox", True, "target"),
        ("sandbox", False, "refused"),
        ("ssh", True, "target"),
        ("ssh", False, "target"),
    ],
)
def test_fetch_runs_where_commands_run_and_offline_refuses(kind: str | None, online: bool, place: str) -> None:
    assert fetch_place(kind, online) == place


HOSTILE_URLS = [
    "https://example.com/a b",
    "https://example.com/'; touch /tmp/x; '",
    'https://example.com/"$(id)"',
    "https://example.com/$(curl evil.test)",
    "https://example.com/`id`",
    "https://example.com/a\nrm -rf /",
    "https://example.com/\u2019; Remove-Item C:\\ \u2019",
]


@pytest.mark.parametrize("url", HOSTILE_URLS)
def test_fetch_command_passes_the_url_as_one_inert_argument_in_bash(url: str) -> None:
    words = shlex.split(fetch_command(url, 1000))
    assert words[:4] == ["python3", "-I", "-c", BOOTSTRAP]
    assert len(words) == 7
    assert base64.b64decode(words[5], validate=True).decode() == url
    assert words[6] == "1000"


@pytest.mark.parametrize("url", HOSTILE_URLS)
def test_fetch_command_quotes_only_the_fixed_bootstrap_in_powershell(url: str) -> None:
    command = fetch_command(url, 1000, "windows")
    prefix = f"python -I -c '{BOOTSTRAP}' "
    assert command.startswith(prefix)
    rest = command.removeprefix(prefix).split(" ")
    assert len(rest) == 3
    assert all(set(word) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=") for word in rest)
    assert base64.b64decode(rest[1]).decode() == url


@pytest.mark.parametrize("limit", [0, -1])
def test_fetch_command_refuses_a_limit_that_is_not_positive(limit: int) -> None:
    with pytest.raises(ValueError):
        fetch_command("https://example.com/", limit)


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1/", "http://[::1]/", "http://169.254.169.254/latest", "http://10.0.0.1/", "http://localhost/"],
)
def test_the_fetch_command_itself_refuses_private_destinations(url: str) -> None:
    words = shlex.split(fetch_command(url, 1000))
    completed = subprocess.run([sys.executable, *words[1:]], capture_output=True, timeout=30, check=False)
    head = json.loads(completed.stdout.partition(b"\n")[0])
    assert head == {"error": "The destination is not a public address.", "status": None}


class FakeExecutor:
    """Answers every capture with a prepared result and remembers the command."""

    def __init__(self, result: CapturedResult) -> None:
        self.result = result
        self.commands: list[str] = []

    async def capture(self, command: str, timeout: float) -> CapturedResult:
        self.commands.append(command)
        return self.result


def head_line(**fields: object) -> bytes:
    return json.dumps(fields).encode() + b"\n"


def test_a_page_fetched_in_the_runner_gives_its_type_text_and_final_url() -> None:
    body = b"<p>hello</p>"
    executor = FakeExecutor(
        CapturedResult(0, head_line(content_type="text/html", final_url="https://example.com/b") + body, b"")
    )
    answer = asyncio.run(fetch_with(executor, "https://example.com/a", 1000, "linux", "the runner"))
    assert answer == ("text/html", "<p>hello</p>", "https://example.com/b")
    assert len(executor.commands) == 1 and BOOTSTRAP in executor.commands[0]


def test_a_page_longer_than_the_limit_is_cut_at_the_limit() -> None:
    executor = FakeExecutor(CapturedResult(0, head_line(content_type="text/plain", final_url="") + b"x" * 50, b""))
    _, text, final = asyncio.run(fetch_with(executor, "https://example.com/", 10, "linux", "the runner"))
    assert (text, final) == ("x" * 10, "https://example.com/")


@pytest.mark.parametrize(
    ("result", "message"),
    [
        (
            CapturedResult(0, head_line(error="The destination is not a public address.", status=None), b""),
            "The destination is not a public address.",
        ),
        (CapturedResult(0, head_line(error="HTTP 404", status=404), b""), "HTTP 404"),
        (CapturedResult(None, b"", b"", too_large=True), "returned more than YAAP reads"),
        (CapturedResult(None, b"", b"", timed_out=True), "took longer than 60 seconds"),
        (CapturedResult(127, b"", b"python3: not found"), "needs Python 3"),
        (CapturedResult(1, b"not json", b"Traceback: boom"), "failed: Traceback: boom"),
    ],
    ids=["private-redirect", "http-error", "too-large", "timeout", "no-python", "garbage"],
)
def test_a_fetch_that_fails_in_the_runner_raises_with_the_reason(result: CapturedResult, message: str) -> None:
    with pytest.raises(FetchError, match=message):
        asyncio.run(fetch_with(FakeExecutor(result), "https://example.com/", 1000, "linux", "the runner"))


def test_a_http_error_keeps_its_status() -> None:
    executor = FakeExecutor(CapturedResult(0, head_line(error="HTTP 404", status=404), b""))
    with pytest.raises(FetchError) as caught:
        asyncio.run(fetch_with(executor, "https://example.com/", 1000, "linux", "the runner"))
    assert caught.value.status == 404


@pytest.mark.parametrize(
    ("kind", "message"), [("runner", "runner has no network"), ("sandbox", "sandbox has no network")]
)
def test_an_offline_place_refuses_a_web_fetch_with_the_clear_message(kind: str, message: str) -> None:
    run = SimpleNamespace(target=SimpleNamespace(kind=kind, spec=SimpleNamespace(network=False)))
    with pytest.raises(ToolError, match=message):
        asyncio.run(fetch_for(run, "https://example.com/"))  # type: ignore[arg-type] # a stand-in for the run
