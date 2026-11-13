"""Captured command output is bounded: a stream past its cap stops the command and reports `too_large`."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.targets import base
from app.targets.base import CapturedResult, OutputTooLargeError, read_outputs
from app.targets.container import LocalExecutor
from app.targets.ssh import SshExecutor

OUT_CAP = 1000
ERR_CAP = 100


class FakeStream:
    """A byte stream that gives its data in chunks of at most `chunk` bytes, as a channel does."""

    def __init__(self, data: bytes, chunk: int = 64) -> None:
        self.data = data
        self.chunk = chunk

    async def read(self, n: int = -1, /) -> bytes:
        size = self.chunk if n < 0 else min(n, self.chunk)
        part, self.data = self.data[:size], self.data[size:]
        return part


@pytest.fixture(autouse=True)
def small_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base, "CAPTURE_LIMIT", OUT_CAP)
    monkeypatch.setattr(base, "CAPTURE_ERROR_LIMIT", ERR_CAP)


CASES = [
    pytest.param(OUT_CAP + 1, 0, True, id="stdout-over-cap"),
    pytest.param(0, ERR_CAP + 1, True, id="stderr-over-cap"),
    pytest.param(OUT_CAP + 1, ERR_CAP + 1, True, id="both-over-cap"),
    pytest.param(OUT_CAP, ERR_CAP, False, id="both-exactly-at-cap"),
    pytest.param(10, 10, False, id="both-within-cap"),
]


@pytest.mark.parametrize(("out_size", "err_size", "too_large"), CASES)
def test_read_outputs_refuses_a_stream_past_its_cap(out_size: int, err_size: int, too_large: bool) -> None:
    streams = (FakeStream(b"o" * out_size), FakeStream(b"e" * err_size))
    if too_large:
        with pytest.raises(OutputTooLargeError):
            asyncio.run(read_outputs(*streams))
    else:
        assert asyncio.run(read_outputs(*streams)) == (b"o" * out_size, b"e" * err_size)


@pytest.mark.parametrize(("out_size", "err_size", "too_large"), CASES)
def test_local_capture_reports_too_large_and_keeps_nothing(
    tmp_path: Path, out_size: int, err_size: int, too_large: bool
) -> None:
    script = tmp_path / "emit.py"
    script.write_text(
        "import sys\n"
        f"sys.stdout.write('o' * {out_size})\n"
        "sys.stdout.flush()\n"
        f"sys.stderr.write('e' * {err_size})\n",
        encoding="utf-8",
    )
    executor = LocalExecutor(str(tmp_path))
    result = asyncio.run(executor.capture(f'"{sys.executable}" "{script}"', 30))
    assert result.too_large is too_large
    if too_large:
        assert (result.exit_code, result.stdout, result.stderr, result.timed_out) == (None, b"", b"", False)
    else:
        assert (result.exit_code, result.stdout, result.stderr) == (0, b"o" * out_size, b"e" * err_size)


class FakeProcess:
    def __init__(self, out_size: int, err_size: int) -> None:
        self.stdout = FakeStream(b"o" * out_size)
        self.stderr = FakeStream(b"e" * err_size)
        self.closed = False

    async def wait(self, check: bool = True) -> SimpleNamespace:
        return SimpleNamespace(exit_status=0)

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(("out_size", "err_size", "too_large"), CASES)
def test_ssh_capture_reports_too_large_and_closes_the_channel(
    monkeypatch: pytest.MonkeyPatch, out_size: int, err_size: int, too_large: bool
) -> None:
    process = FakeProcess(out_size, err_size)

    async def start(
        command: str, env: dict[str, str], session: object = None, separate_stderr: bool = False
    ) -> FakeProcess:
        assert separate_stderr
        return process

    executor = SshExecutor(SimpleNamespace(), "/work")  # type: ignore[arg-type] # the fake target is never used
    monkeypatch.setattr(executor, "start", start)
    result: CapturedResult = asyncio.run(executor.capture("echo", 30))
    assert result.too_large is too_large
    assert process.closed is too_large
    if too_large:
        assert (result.exit_code, result.stdout, result.stderr, result.timed_out) == (None, b"", b"", False)
    else:
        assert (result.exit_code, result.stdout, result.stderr) == (0, b"o" * out_size, b"e" * err_size)
