"""Web fetches run where the run's commands run, refuse when that place is offline, and carry no shell input."""

import base64
import json
import shlex
import subprocess
import sys

import pytest

from app.websearch.runner_fetch import BOOTSTRAP, fetch_command, fetch_place


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
