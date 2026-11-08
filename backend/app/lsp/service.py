"""Code intelligence for one agent run: where its project's language servers run, for which
languages, and what the file tools add from them.

A run gets a `CodeIntel` when it works on a project whose commands run in YAAP's process (runner off), the
environment's runner or the project's sandbox, and the project has files of a language whose server can run there.
On a computer over SSH the project's files are not in YAAP, so a run there gets none.

- The code tools (app/agent/code_tools.py) start the server of a file's language when they need it.
- After `edit_file` and `write_file`, when a server for the file's language already runs, the file's new diagnostics
  are added to the result (`after_write`), waiting at most DIAGNOSTICS_WAIT seconds for the server.
- `read_file(outline=true)` takes the document symbols of a running server, the simple parsers otherwise (`outliner`).
"""

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from app.lsp.client import LanguageClient
from app.lsp.connection import LspError, LspUnavailable
from app.lsp.paths import PathMap
from app.lsp.pool import LANGUAGE_SERVERS, LanguageServerPool
from app.lsp.process import LOCAL, Place
from app.lsp.results import diagnostic_rows, outline_from_symbols
from app.lsp.servers import SERVERS, ServerSpec, project_servers, spec_for
from app.outline.symbols import Outline, Outliner
from app.runner.service import RunnerTarget, runner_path
from app.sandbox.service import SANDBOX_ROOT, SandboxTarget
from app.targets.base import TargetError
from app.targets.ssh import SshTarget

# After an edit: how long to wait for the server's diagnostics of the new text, and how many to show.
DIAGNOSTICS_WAIT = 3.0
DIAGNOSTICS_SHOWN = 5
OUTLINE_SECONDS = 10.0


def place_for(project_folder: Path, runtime: SshTarget | None) -> Place | None:
    """Where the servers of a project run: where its commands run. None on a computer over SSH."""
    if runtime is None or getattr(runtime, "kind", "") == LOCAL:
        return Place(LOCAL, PathMap(str(project_folder.resolve()), windows=sys.platform == "win32"))
    if isinstance(runtime, RunnerTarget):
        try:
            root = runner_path(runtime.environment_dir, str(project_folder))
        except TargetError:
            return None
        return Place(runtime.key, PathMap(root), runtime)
    if isinstance(runtime, SandboxTarget):
        return Place(runtime.key, PathMap(SANDBOX_ROOT), runtime)
    return None


def installed_locally(spec: ServerSpec) -> bool:
    return shutil.which(spec.program) is not None


@dataclass(frozen=True)
class CodeIntel:
    place: Place
    # The servers that can run for this run's project, one per language.
    servers: tuple[ServerSpec, ...]
    pool: LanguageServerPool = LANGUAGE_SERVERS

    @property
    def languages(self) -> str:
        return ", ".join(f"{spec.language} ({spec.name})" for spec in self.servers)

    def spec_for(self, path: str) -> ServerSpec | None:
        return spec_for(path, self.servers)

    def require(self, path: str) -> ServerSpec:
        spec = self.spec_for(path)
        if spec is None:
            raise LspUnavailable(
                f"No language server runs for {path} in this project; servers run for: {self.languages}. Use search "
                "and read_file instead."
            )
        return spec

    async def client(self, path: str) -> tuple[LanguageClient, ServerSpec]:
        """The running server for the file's language, started when needed."""
        spec = self.require(path)
        return await self.pool.get(self.place, spec), spec

    def running(self, path: str) -> LanguageClient | None:
        spec = self.spec_for(path)
        return self.pool.running(self.place, spec) if spec is not None else None

    async def after_write(self, path: str, text: str) -> str:
        """The file's diagnostics after a write, for the tool's result; empty when no server runs for it."""
        client = self.running(path)
        if client is None:
            return ""
        name = client.spec.name
        try:
            after = client.generation
            uri, version, sent = await client.sync(path, text)
            # Unchanged text brings no new diagnostics; the last ones are those of this text.
            if sent:
                published = await client.wait_diagnostics(uri, version, after, DIAGNOSTICS_WAIT)
            else:
                published = client.current(uri)
        except LspError:
            return ""
        if published is None:
            return f"\n\n[{name} sent no diagnostics within {DIAGNOSTICS_WAIT:g} seconds; call diagnostics to check.]"
        if not published.diagnostics:
            return f"\n\n[{name}: no problems in this file.]"
        count = len(published.diagnostics)
        rows = diagnostic_rows(published.diagnostics, DIAGNOSTICS_SHOWN)
        return f"\n\n[{name} reports {count} problem{'s' if count > 1 else ''} in {path} now:]\n" + "\n".join(rows)

    def outliner(self, fallback: Outliner) -> Outliner:
        return ServerOutliner(self, fallback)


class ServerOutliner:
    """Implements `Outliner` with a running server's document symbols, and the fallback where none runs."""

    def __init__(self, intel: CodeIntel, fallback: Outliner) -> None:
        self.intel = intel
        self.fallback = fallback

    async def outline(self, path: str, text: str) -> Outline | None:
        client = self.intel.running(path)
        if client is None:
            return await self.fallback.outline(path, text)
        try:
            outline = await document_outline(client, path, text, OUTLINE_SECONDS)
        except LspError:
            return await self.fallback.outline(path, text)
        return outline if outline.symbols else await self.fallback.outline(path, text)


async def document_outline(client: LanguageClient, path: str, text: str, timeout: float) -> Outline:
    uri, _, _ = await client.sync(path, text)
    result = await client.request("textDocument/documentSymbol", {"textDocument": {"uri": uri}}, timeout)
    return outline_from_symbols(result)


def code_intel_for(
    project_folder: Path | None,
    runtime: SshTarget | None,
    skip: frozenset[str],
    servers: tuple[ServerSpec, ...] = SERVERS,
    pool: LanguageServerPool = LANGUAGE_SERVERS,
) -> CodeIntel | None:
    """The run's code intelligence, or None when no server can run for its project's languages where it works."""
    if project_folder is None or not project_folder.is_dir():
        return None
    place = place_for(project_folder, runtime)
    if place is None:
        return None
    local = place.remote is None
    found = tuple(
        spec
        for spec in project_servers(project_folder, skip, servers)
        if pool.can_run(place, spec, installed_locally(spec) if local else False)
    )
    return CodeIntel(place, found, pool) if found else None
