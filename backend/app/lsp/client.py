"""One running language server: initialize, the documents YAAP opened in it, its diagnostics, shutdown.

YAAP sends whole documents (full text sync): a document is opened with the text the tool read and changed with the
new text whenever it differs, so the server always knows what is on disk now, also after `run_command` changed a file.
Diagnostics are pushed by the server (`textDocument/publishDiagnostics`) and kept per document; a tool can wait,
briefly, for the diagnostics of the version it just sent.
"""

import asyncio
import collections
import contextlib
import os
from dataclasses import dataclass
from typing import Any

from app.lsp.connection import JsonRpcConnection, LspError, MethodNotFound
from app.lsp.paths import PathMap
from app.lsp.process import ServerProcess
from app.lsp.servers import ServerSpec, suffix
from app.version import VERSION

START_SECONDS = 60
REQUEST_SECONDS = 30
STOP_SECONDS = 3
CLOSED_CHECK_SECONDS = 0.25
# Documents kept open per server; the least recently used is closed beyond this.
MAX_OPEN_DOCUMENTS = 60
CLIENT_INFO = {"name": "YAAP", "version": VERSION}
# The symbol kinds YAAP understands: all 26 of the protocol.
SYMBOL_KINDS = list(range(1, 27))


def client_capabilities() -> dict[str, Any]:
    return {
        "general": {"positionEncodings": ["utf-16"]},
        "workspace": {"workspaceFolders": True, "configuration": True, "applyEdit": False},
        "textDocument": {
            "synchronization": {"didSave": False, "dynamicRegistration": False},
            "publishDiagnostics": {"relatedInformation": False, "versionSupport": True},
            "definition": {"linkSupport": True},
            "references": {},
            "rename": {"prepareSupport": False},
            "documentSymbol": {"hierarchicalDocumentSymbolSupport": True, "symbolKind": {"valueSet": SYMBOL_KINDS}},
        },
        "window": {"workDoneProgress": False},
    }


@dataclass
class OpenDocument:
    version: int
    text: str


@dataclass(frozen=True)
class Published:
    """The diagnostics the server last published for a document, and for which version (None: it did not say)."""

    diagnostics: tuple[dict[str, Any], ...]
    version: int | None
    generation: int


class LanguageClient:
    def __init__(self, spec: ServerSpec, process: ServerProcess, paths: PathMap, local: bool) -> None:
        self.spec = spec
        # A server watches the process id it is given and exits when it is gone; on another machine YAAP's id names
        # nothing (or something else), so a server in a runner gets none.
        self.process_id = os.getpid() if local else None
        self.process = process
        self.paths = paths
        self.connection = JsonRpcConnection(process.stdout, process.stdin, self.on_notification, self.on_request)
        self.documents: collections.OrderedDict[str, OpenDocument] = collections.OrderedDict()
        self.published: dict[str, Published] = {}
        self.generation = 0
        # Set and replaced at every publishDiagnostics, which wakes whoever waits for diagnostics.
        self.update = asyncio.Event()
        self.capabilities: dict[str, Any] = {}
        self.loop = asyncio.get_running_loop()

    @property
    def alive(self) -> bool:
        return self.process.alive and not self.connection.closed

    def failure(self) -> str:
        """Why the server is not running, with the last lines of its standard error."""
        reason = self.connection.close_reason or "The server stopped."
        code = self.process.exit_code
        tail = self.process.stderr_tail()
        return reason + (f" Exit status {code}." if code is not None else "") + (f"\n{tail}" if tail else "")

    async def start(self, timeout: float = START_SECONDS) -> None:
        self.connection.start()
        params = {
            "processId": self.process_id,
            "clientInfo": CLIENT_INFO,
            "rootUri": self.paths.uri(),
            "rootPath": self.paths.root,
            "workspaceFolders": [{"uri": self.paths.uri(), "name": "project"}],
            "capabilities": client_capabilities(),
            "initializationOptions": self.spec.initialization_options,
        }
        result = await self.connection.request("initialize", params, timeout)
        self.capabilities = dict((result or {}).get("capabilities") or {})
        await self.connection.notify("initialized", {})

    async def request(self, method: str, params: dict[str, Any], timeout: float = REQUEST_SECONDS) -> Any:
        return await self.connection.request(method, params, timeout)

    # --- Documents ----------------------------------------------------------------------------------------------------

    async def sync(self, relative: str, text: str) -> tuple[str, int, bool]:
        """Open the document with this text, or send the text when it changed; its URI, its version, and whether
        anything was sent (the server publishes new diagnostics only then)."""
        uri = self.paths.uri(relative)
        document = self.documents.get(uri)
        if document is None:
            document = OpenDocument(1, text)
            self.documents[uri] = document
            language_id = self.spec.language_ids.get(suffix(relative), self.spec.language)
            item = {"uri": uri, "languageId": language_id, "version": 1, "text": text}
            await self.connection.notify("textDocument/didOpen", {"textDocument": item})
            await self.close_oldest()
            sent = True
        elif document.text != text:
            document.version += 1
            document.text = text
            change = {"textDocument": {"uri": uri, "version": document.version}, "contentChanges": [{"text": text}]}
            await self.connection.notify("textDocument/didChange", change)
            sent = True
        else:
            sent = False
        self.documents.move_to_end(uri)
        return uri, document.version, sent

    async def close_oldest(self) -> None:
        while len(self.documents) > MAX_OPEN_DOCUMENTS:
            uri, _ = self.documents.popitem(last=False)
            self.published.pop(uri, None)
            await self.connection.notify("textDocument/didClose", {"textDocument": {"uri": uri}})

    def current(self, uri: str) -> Published | None:
        return self.published.get(uri)

    async def wait_diagnostics(self, uri: str, version: int, after: int, timeout: float) -> Published | None:
        """The diagnostics of `version` (or later) published after generation `after`; None when none came in time."""

        def arrived() -> bool:
            found = self.published.get(uri)
            return (
                found is not None
                and found.generation > after
                and (found.version is None or found.version >= version)
            ) or self.connection.closed

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not arrived():
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            # In slices, so a server that stops while YAAP waits is noticed soon.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.update.wait(), min(remaining, CLOSED_CHECK_SECONDS))
        found = self.published.get(uri)
        return found if found is not None and found.generation > after else None

    # --- Messages from the server -------------------------------------------------------------------------------------

    def on_notification(self, method: str, params: Any) -> None:
        if method != "textDocument/publishDiagnostics" or not isinstance(params, dict):
            return
        uri = str(params["uri"])
        version = params.get("version")
        diagnostics = tuple(item for item in params.get("diagnostics") or [] if isinstance(item, dict))
        self.generation += 1
        self.published[uri] = Published(diagnostics, version if isinstance(version, int) else None, self.generation)
        self.wake()

    def wake(self) -> None:
        self.update.set()
        self.update = asyncio.Event()

    def on_request(self, method: str, params: Any) -> Any:
        """Requests a server sends its client. YAAP has no settings to give and accepts every registration."""
        if method == "workspace/configuration":
            items = params.get("items") if isinstance(params, dict) else None
            return [None for _ in items or []]
        if method == "workspace/workspaceFolders":
            return [{"uri": self.paths.uri(), "name": "project"}]
        if method in ("client/registerCapability", "client/unregisterCapability", "window/workDoneProgress/create"):
            return None
        if method == "window/showMessageRequest":
            return None
        raise MethodNotFound(method)

    # --- Stopping -----------------------------------------------------------------------------------------------------

    async def stop(self) -> None:
        """shutdown, then exit; the process is killed when it does not end by itself."""
        if self.alive:
            with contextlib.suppress(LspError):
                await self.connection.request("shutdown", None, STOP_SECONDS)
                await self.connection.notify("exit", None)
            await self.process.wait(STOP_SECONDS)
        self.process.kill()
        await self.connection.stop_reading()
        self.wake()

    def kill(self) -> None:
        """Stop without the protocol: for a server that stopped, or one of an event loop that is gone and cannot be
        awaited (its futures belong to that loop, hence the RuntimeError)."""
        with contextlib.suppress(OSError, RuntimeError):
            self.process.kill()
        with contextlib.suppress(RuntimeError):
            self.connection.mark_closed("The server was stopped.")
