"""A screenshot of a preview through the agent's browser, where the dev server runs.

The agent's browser (app/browser) runs in the environment's runner, beside the processes the agent starts there, so
it opens a dev server on `http://localhost:<port>` directly and never goes through the preview listener. The panel's
screenshot button and the eval checks both call `screenshot_preview()` with a `PreviewBrowser`;
`McpPreviewBrowser` is the real one (the conversation's Playwright MCP browser), tests pass a fake.
"""

import base64
import binascii
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.agent.processes import ManagedProcess
from app.browser.service import IDLE_SECONDS, browser_dir, is_local, scope, server_config
from app.mcp_client.client import McpError
from app.mcp_client.manager import Connection, McpManager
from app.mcp_client.remote import PlaceFactory
from app.runner.service import RUNNER

NAVIGATE = "browser_navigate"
TAKE_SCREENSHOT = "browser_take_screenshot"


class ScreenshotError(Exception):
    """No screenshot: the URL is not a local preview, the process runs where the browser cannot see it, or the
    browser failed. The message says which."""


@dataclass(frozen=True)
class Screenshot:
    media_type: str
    data: bytes


class PreviewBrowser(Protocol):
    """A browser that can open a page and take a screenshot of it."""

    async def open(self, url: str) -> None: ...

    async def screenshot(self) -> Screenshot: ...


def preview_url(port: int, path: str = "/") -> str:
    """The address of a dev server as the browser beside it sees it; `path` stays on that server."""
    if not 1 <= port <= 65535:
        raise ScreenshotError("The process has no valid port.")
    clean = path.strip() or "/"
    if not clean.startswith("/") or clean.startswith("//") or "\\" in clean or any(c in clean for c in "\r\n\t "):
        raise ScreenshotError("The path must start with / and stay on the dev server.")
    return f"http://localhost:{port}{clean}"


def browser_sees(managed: ManagedProcess) -> bool:
    """Whether the agent's browser reaches the process on localhost: it runs in the runner (or, with the runner off,
    both run in YAAP's process). A sandbox or a computer over SSH is another machine for the browser."""
    return managed.target is None or managed.target.kind == RUNNER


async def screenshot_preview(browser: PreviewBrowser, url: str) -> Screenshot:
    """Open a local preview URL in the browser and take a screenshot of it."""
    if not is_local(url):
        raise ScreenshotError("Only a preview on localhost can be photographed this way.")
    await browser.open(url)
    return await browser.screenshot()


class McpPreviewBrowser:
    """The conversation's own Playwright MCP browser, the one the agent uses."""

    def __init__(self, connection: Connection, timeout: float = 60) -> None:
        self.connection = connection
        self.timeout = timeout

    async def call(self, tool: str, arguments: dict[str, str]) -> tuple[str, tuple[tuple[str, str], ...]]:
        try:
            result = await self.connection.call(tool, arguments, self.timeout)
        except McpError as error:
            raise ScreenshotError(f"The browser: {error}") from error
        if result.is_error:
            raise ScreenshotError(f"The browser: {result.text or 'the call failed'}")
        return result.text, result.images

    async def open(self, url: str) -> None:
        await self.call(NAVIGATE, {"url": url})

    async def screenshot(self) -> Screenshot:
        _, images = await self.call(TAKE_SCREENSHOT, {})
        if not images:
            raise ScreenshotError("The browser returned no image.")
        media_type, data = images[0]
        try:
            return Screenshot(media_type, base64.b64decode(data, validate=True))
        except binascii.Error as error:
            raise ScreenshotError("The browser returned an image that cannot be read.") from error


def conversation_browser(
    manager: McpManager, command: list[str], data_dir: Path, conversation_id: int, place: PlaceFactory | None
) -> McpPreviewBrowser:
    """The browser of a conversation, in the runner when `place` is given; it starts on its first call."""
    server = server_config(command, data_dir, conversation_id, in_runner=place is not None)
    cwd = None if place is not None else str(browser_dir(data_dir, conversation_id))
    if place is None:
        browser_dir(data_dir, conversation_id).mkdir(parents=True, exist_ok=True)
    connection = manager.connection(server, scope(conversation_id), cwd=cwd, place=place)
    connection.idle_seconds = IDLE_SECONDS
    return McpPreviewBrowser(connection)
