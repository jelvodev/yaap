"""A hardened server process: not dumpable, no master key left in the environment, a private key file."""

import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from app.config import SECRET_VARIABLES, load_settings, open_to_others
from app.core.process_hardening import PR_SET_DUMPABLE, linux_prctl, mark_not_dumpable

BACKEND = Path(__file__).resolve().parents[1]


class FakePrctl:
    def __init__(self, result: int) -> None:
        self.result = result
        self.calls: list[tuple[int, ...]] = []

    def __call__(self, option: int, arg2: int, arg3: int, arg4: int, arg5: int, /) -> int:
        self.calls.append((option, arg2, arg3, arg4, arg5))
        return self.result


def test_the_process_is_marked_not_dumpable() -> None:
    prctl = FakePrctl(0)
    assert mark_not_dumpable(prctl) is True
    assert prctl.calls == [(PR_SET_DUMPABLE, 0, 0, 0, 0)]


def test_a_failing_prctl_is_logged_and_yaap_starts_anyway(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert mark_not_dumpable(FakePrctl(-1)) is False
    assert "not dumpable" in caplog.text


@pytest.mark.parametrize("platform", ["win32", "darwin", "cygwin"])
def test_there_is_nothing_to_mark_outside_linux(platform: str) -> None:
    assert linux_prctl(platform) is None
    assert mark_not_dumpable(None) is False


CHECK = """
import ctypes, subprocess, sys
from app.core.process_hardening import PR_GET_DUMPABLE, harden_process, linux_prctl
assert harden_process()
own = linux_prctl()(PR_GET_DUMPABLE, 0, 0, 0, 0)
child = subprocess.run(
    [sys.executable, "-c", "import ctypes; print(ctypes.CDLL(None).prctl(3, 0, 0, 0, 0))"],
    capture_output=True, text=True, check=True,
).stdout.strip()
print(own, child)
"""


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="prctl exists only on Linux")
def test_on_linux_yaap_is_not_dumpable_and_what_it_starts_is() -> None:
    result = subprocess.run(
        [sys.executable, "-c", CHECK], cwd=BACKEND, capture_output=True, text=True, check=True, timeout=60
    )
    assert result.stdout.split() == ["0", "1"]


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (0o100600, False),
        (0o100400, False),
        (0o100700, False),
        (0o100640, True),
        (0o100604, True),
        (0o100644, True),
        (0o100660, True),
        (0o100602, True),
        (0o100777, True),
    ],
)
def test_a_key_file_must_be_private_to_its_owner(mode: int, expected: bool) -> None:
    assert open_to_others(mode) is expected


@pytest.mark.skipif(os.name != "posix", reason="Windows has no permission bits to check")
def test_a_key_file_readable_by_others_gives_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    key_file = tmp_path / "key"
    key_file.write_text(Fernet.generate_key().decode(), encoding="utf-8")
    monkeypatch.setenv("YAAP_MASTER_KEY_FILE", str(key_file))
    key_file.chmod(0o600)
    with caplog.at_level(logging.WARNING):
        load_settings()
    assert "readable by its owner only" not in caplog.text
    key_file.chmod(0o644)
    with caplog.at_level(logging.WARNING):
        load_settings()
    assert "readable by its owner only" in caplog.text


@pytest.mark.parametrize("name", SECRET_VARIABLES)
def test_the_master_key_leaves_the_environment_once_read(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    for other in SECRET_VARIABLES:
        monkeypatch.delenv(other, raising=False)
    monkeypatch.delenv("YAAP_MASTER_KEY_FILE", raising=False)
    key = Fernet.generate_key().decode()
    if name != "YAAP_MASTER_KEY_PREVIOUS":
        monkeypatch.setenv(name, key)
    else:
        monkeypatch.setenv("YAAP_MASTER_KEY", key)
        monkeypatch.setenv(name, Fernet.generate_key().decode())
    settings = load_settings()
    assert settings.master_key == key.encode()
    assert not any(variable in os.environ for variable in SECRET_VARIABLES)


def test_a_running_yaap_has_no_master_key_in_its_environment(client) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/api/v1/health").status_code == 200
    assert not any(variable in os.environ for variable in SECRET_VARIABLES)
