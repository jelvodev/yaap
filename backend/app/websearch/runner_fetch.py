"""Web fetches where the run's commands run: in the runner, the project's sandbox or on a computer over SSH.

YAAP's own process can reach services the agent should not (its own API, the sandbox helper, the LAN), so `web_fetch`
runs where the run's commands run and reaches only what that place may reach. The same `safe_fetch` module does the
work there: YAAP sends its source with the command and runs it with the place's Python 3, so the public-address and
per-hop redirect checks apply inside the runner too (which can reach the LAN and `host.docker.internal`). Only a run
without a runner (a conversation outside a project, or YAAP_RUNNER=off) fetches in YAAP's process.
"""

import base64
import json
from functools import cache
from pathlib import Path
from typing import Literal

from app.sandbox.service import RUNNER, SANDBOX
from app.targets.base import CapturedResult, CommandExecutor
from app.targets.container import CONTAINER
from app.targets.ssh import WINDOWS, shell_quote
from app.websearch import safe_fetch
from app.websearch.safe_fetch import FetchError

FetchPlace = Literal["runner", "target", "process", "refused"]

# Each hop has its own 20-second limit in safe_fetch; this bounds the whole fetch with its redirects.
FETCH_TIMEOUT = 60.0
# Room for the JSON line before the body.
HEAD_LIMIT = 16 * 1024

OFFLINE = {
    RUNNER: "The runner has no network access; switch it on in the environment settings.",
    SANDBOX: "This project's sandbox has no network access; switch it on in the project's sandbox settings.",
}
# Runs the module source given as the first argument; base64 keeps every shell and PowerShell from reading into it.
BOOTSTRAP = "import base64,sys;exec(base64.b64decode(sys.argv.pop(1)))"


def fetch_place(kind: str | None, online: bool) -> FetchPlace:
    """Where a run's web fetch runs, from the kind of the run's target (None: commands run in YAAP's process) and
    whether that place has network access. An offline runner or sandbox refuses; YAAP never fetches for it."""
    if kind is None or kind == CONTAINER.kind:
        return "process"
    if kind in (RUNNER, SANDBOX) and not online:
        return "refused"
    return "runner" if kind == RUNNER else "target"


@cache
def module_source() -> bytes:
    return Path(safe_fetch.__file__).read_bytes()


def fetch_command(url: str, limit: int, system: str = "linux") -> str:
    """The command that fetches `url` with the place's Python 3. The source and the URL travel in base64, so the
    only quoted part is the fixed bootstrap."""
    if limit <= 0:
        raise ValueError("The limit must be a positive number of bytes.")
    python = "python" if system == WINDOWS else "python3"
    source = base64.b64encode(module_source()).decode()
    encoded_url = base64.b64encode(url.encode()).decode()
    return f"{python} -I -c {shell_quote(system, BOOTSTRAP)} {source} {encoded_url} {int(limit)}"


def read_answer(result: CapturedResult, limit: int, where: str) -> tuple[str, bytes, str]:
    """Content type, body (at most `limit` bytes) and final URL from the command's output, or FetchError."""
    if result.timed_out:
        raise FetchError(f"The fetch in {where} took longer than {int(FETCH_TIMEOUT)} seconds.")
    if result.too_large:
        raise FetchError(f"The fetch in {where} returned more than YAAP reads.")
    line, _, body = result.stdout.partition(b"\n")
    try:
        head = json.loads(line.decode(errors="replace").strip()) if len(line) <= HEAD_LIMIT else None
    except ValueError:
        head = None
    if not isinstance(head, dict):
        if result.exit_code in (9009, 127) or b"not found" in result.stderr or b"not recognized" in result.stderr:
            raise FetchError(f"Fetching a page in {where} needs Python 3 there, which it does not have.")
        detail = result.stderr.decode(errors="replace").strip()[-300:] or f"exit code {result.exit_code}"
        raise FetchError(f"The fetch in {where} failed: {detail}")
    if "error" in head:
        status = head.get("status")
        raise FetchError(str(head["error"]), status if isinstance(status, int) else None)
    return str(head.get("content_type") or ""), body[:limit], str(head.get("final_url") or "")


async def fetch_with(executor: CommandExecutor, url: str, limit: int, system: str, where: str) -> tuple[str, str, str]:
    """Fetch a public page through a run's executor: content type, text and final URL, as `fetch_public`."""
    result = await executor.capture(fetch_command(url, limit, system), FETCH_TIMEOUT)
    content_type, body, final_url = read_answer(result, limit, where)
    return content_type, body.decode(errors="replace"), final_url or url
