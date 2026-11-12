"""The agent's browser.

YAAP runs Microsoft's Playwright MCP server (`@playwright/mcp`, pinned in the sandbox image, unmodified) through its
MCP client: one headless Chromium per conversation, in the environment's runner, with its own profile
there under `/home/yaap/.yaap-browser/<conversation>/`. Chromium is not part of YAAP's image; only with the runner
switched off (YAAP_RUNNER=off) does it run in YAAP's process, with its profile under `data/browser/<conversation>/`.
It works with page snapshots and element references. The tools are narrowed to BROWSER_TOOLS and named
`browser__<tool>`.

- Pages on localhost (the project's own dev servers, which run in the same runner) open without asking; other sites ask per call, unless their
  domain is in `web_allowlist`. What other sites show counts as outside content.
- Typing a secret works with `{{secret:NAME}}` in the text: YAAP fills in the value, and every output is masked.
- Screenshots come back over MCP; YAAP keeps them under the conversation in its data folder and shows them in the chat.
- A conversation's browser stops after IDLE_SECONDS without use and when the conversation is removed.
"""

import base64
import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import anyio
import asyncssh

from app.agent.guard import note_outside
from app.agent.policy import url_allowed
from app.agent.runs import AgentRun
from app.agent.toolkit import Tool, ToolError, ToolOutput
from app.mcp_client.client import McpError
from app.mcp_client.config import ServerConfig
from app.mcp_client.manager import McpManager, McpTool, tool_name
from app.mcp_client.remote import PlaceFactory
from app.targets.base import TargetError
from app.targets.ssh import shell_quote
from app.secrets.service import fill_in, placeholder_names

logger = logging.getLogger(__name__)

SERVER = "browser"
IDLE_SECONDS = 900
PLAYWRIGHT_COMMAND = "playwright-mcp"
# In the runner: where the sandbox image keeps Chromium, and the conversations' profiles.
RUNNER_BROWSERS = "/opt/ms-playwright"
RUNNER_PROFILES = "/home/yaap/.yaap-browser"
# What the agent may do with the browser; running scripts, uploading files and installing are left out.
BROWSER_TOOLS = frozenset(
    {
        "browser_navigate",
        "browser_navigate_back",
        "browser_snapshot",
        "browser_click",
        "browser_type",
        "browser_fill_form",
        "browser_select_option",
        "browser_press_key",
        "browser_hover",
        "browser_wait_for",
        "browser_take_screenshot",
        "browser_tabs",
        "browser_close",
        "browser_console_messages",
        "browser_handle_dialog",
    }
)
READ_ONLY = frozenset({"browser_snapshot", "browser_take_screenshot", "browser_console_messages", "browser_tabs", "browser_wait_for"})
TYPING = frozenset({"browser_type", "browser_fill_form"})
FILENAME_TOOLS = frozenset({"browser_take_screenshot", "browser_snapshot", "browser_console_messages"})
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})
PAGE_URL = re.compile(r"Page URL:\s*(\S+)")
NAVIGATE_NOTE = (
    " To check the project's own web app: start its dev server with start_process, then open it here on localhost."
)
SHOT = re.compile(r"^\d+$")


def browser_dir(data_dir: Path, conversation_id: int) -> Path:
    return data_dir / "browser" / str(conversation_id)


def shots_dir(data_dir: Path, conversation_id: int) -> Path:
    return browser_dir(data_dir, conversation_id) / "shots"


def is_local(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host in LOCAL_HOSTS or host.endswith(".localhost")


def scope(conversation_id: int) -> str:
    return f"browser-{conversation_id}"


def find_command(in_runner: bool = False) -> list[str] | None:
    """The Playwright MCP command: in the runner it is part of the sandbox image; in YAAP's process, when installed."""
    if in_runner:
        return [PLAYWRIGHT_COMMAND]
    path = shutil.which(PLAYWRIGHT_COMMAND)
    return [path] if path else None


def runner_folder(conversation_id: int) -> str:
    return f"{RUNNER_PROFILES}/{conversation_id}"


def server_config(command: list[str], data_dir: Path, conversation_id: int, in_runner: bool = False) -> ServerConfig:
    folder = runner_folder(conversation_id) if in_runner else browser_dir(data_dir, conversation_id).as_posix()
    return ServerConfig(
        name=SERVER,
        transport="stdio",
        command=command[0],
        env=(("PLAYWRIGHT_BROWSERS_PATH", RUNNER_BROWSERS),) if in_runner else (),
        args=(
            *command[1:],
            "--headless",
            "--browser",
            "chromium",
            "--user-data-dir",
            f"{folder}/profile",
            "--output-dir",
            f"{folder}/output",
            "--viewport-size",
            "1280x800",
            "--image-responses",
            "allow",
            "--idle-timeout",
            str(IDLE_SECONDS * 1000),
        ),
        start_timeout=30,
        call_timeout=120,
        only_tools=BROWSER_TOOLS,
    )


def save_shots(data_dir: Path, conversation_id: int, images: tuple[tuple[str, str], ...]) -> list[int]:
    folder = shots_dir(data_dir, conversation_id)
    folder.mkdir(parents=True, exist_ok=True)
    numbers = []
    for media_type, data in images:
        existing = [int(path.stem) for path in folder.glob("*.*") if SHOT.match(path.stem)]
        number = max(existing, default=0) + 1
        extension = "jpg" if "jpeg" in media_type else "png"
        (folder / f"{number}.{extension}").write_bytes(base64.b64decode(data))
        numbers.append(number)
    return numbers


def forwarded_arguments(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """The arguments sent on to the browser. A `filename` is dropped: Playwright MCP resolves it against its working
    directory (outside the conversation's output folder) and then returns no image, so the model would not see the
    shot and the chat could not show it. Without it the file lands in the output folder and the image comes back."""
    if tool in FILENAME_TOOLS and "filename" in arguments:
        return {key: value for key, value in arguments.items() if key != "filename"}
    return arguments


def fill_secrets(value: Any, secrets: dict[str, str]) -> Any:
    if isinstance(value, str):
        return fill_in(value, secrets)
    if isinstance(value, list):
        return [fill_secrets(item, secrets) for item in value]
    if isinstance(value, dict):
        return {key: fill_secrets(item, secrets) for key, item in value.items()}
    return value


class BrowserState:
    """Where the conversation's browser is: what the page shows counts as outside content unless it is local."""

    def __init__(self) -> None:
        self.url = ""


def browser_tool(manager: McpManager, ref: McpTool, data_dir: Path, conversation_id: int, state: BrowserState) -> Tool:
    short = ref.tool

    def needs_approval(run: AgentRun, arguments: dict[str, Any]) -> bool:
        if short != "browser_navigate":
            return False
        url = str(arguments.get("url") or "")
        return not (is_local(url) or url_allowed(url, run.policy.domains))

    async def handler(run: AgentRun, arguments: dict[str, Any]) -> str:
        arguments = forwarded_arguments(short, arguments)
        sent = fill_secrets(arguments, run.secrets) if short in TYPING else arguments
        if sent is not arguments and run.on_secret_use is not None:
            used = placeholder_names(json.dumps(arguments))
            if used:
                run.on_secret_use(used, "Typed in the browser")
        try:
            result = await manager.call(ref, sent)
        except McpError as error:
            raise ToolError(f"The browser: {error}") from error
        found = PAGE_URL.findall(result.text)
        if found:
            state.url = found[-1]
        elif short == "browser_navigate":
            state.url = str(arguments.get("url") or state.url)
        if state.url and not is_local(state.url):
            note_outside(run, f"web page {state.url}", result.text)
        text = result.text or "(no output)"
        if result.images:
            numbers = await anyio.to_thread.run_sync(save_shots, data_dir, conversation_id, result.images)
            text += "".join(f"\n[Screenshot: shot {number}]" for number in numbers)
        if result.is_error:
            raise ToolError(text)
        return ToolOutput.with_images(text, list(result.images))

    def describe(arguments: dict[str, Any]) -> str:
        if short == "browser_navigate":
            return f"Open in the browser: {arguments.get('url', '')}"
        shown = json.dumps(arguments, ensure_ascii=False)
        return f"Browser: {short.removeprefix('browser_')} {shown[:200]}"

    description = ref.description.replace(f"[MCP server {SERVER}] ", "[Browser] ")
    if short == "browser_navigate":
        description += NAVIGATE_NOTE
    return Tool(
        ref.name,
        description,
        ref.input_schema,
        "conditional" if short == "browser_navigate" else "auto",
        handler,
        describe=describe,
        scope="always",
        needs_approval=needs_approval,
        read_only=short in READ_ONLY,
        takes_secrets=short in TYPING,
    )


async def browser_tools(
    manager: McpManager, command: list[str], data_dir: Path, conversation_id: int, place: PlaceFactory | None = None
) -> dict[str, Tool]:
    """The browser's tools for a run of this conversation, in the runner when `place` is given. The tool list is read
    once from a running server; the conversation's own browser starts on its first use."""
    server = server_config(command, data_dir, conversation_id, in_runner=place is not None)
    folder = browser_dir(data_dir, conversation_id)
    await anyio.to_thread.run_sync(lambda: folder.mkdir(parents=True, exist_ok=True))
    cwd = None if place is not None else str(folder)
    connection = manager.connection(server, scope(conversation_id), cwd=cwd, place=place)
    connection.idle_seconds = IDLE_SECONDS
    if not connection.raw_tools:
        template = next((item for item in manager.connections.values() if item.server.name == SERVER and item.raw_tools), None)
        if template is not None:
            connection.raw_tools = template.raw_tools
        else:
            try:
                await connection.ensure()
            except McpError as error:
                logger.warning("The browser did not start: %s", error)
                return {}
    state = BrowserState()
    return {ref.name: browser_tool(manager, ref, data_dir, conversation_id, state) for ref in connection.tools()}


async def remove_conversation_browser(
    manager: McpManager, data_dir: Path, conversation_id: int, place: PlaceFactory | None = None
) -> None:
    """Stop the conversation's browser and remove its profile and screenshots. A profile in the runner is removed
    when the runner is running; one in a stopped runner goes when the runner is made again."""
    await manager.stop_scope(scope(conversation_id))
    await anyio.to_thread.run_sync(lambda: shutil.rmtree(browser_dir(data_dir, conversation_id), ignore_errors=True))
    if place is None:
        return
    try:
        runner = await place()
        if not runner.is_open():
            return
        connection = await runner.connection()
        await connection.run(f"rm -rf {shell_quote('linux', runner_folder(conversation_id))}", check=False, timeout=30)
    except (TargetError, asyncssh.Error, OSError, TimeoutError) as error:
        logger.warning("The browser profile of conversation %s in the runner was not removed: %s", conversation_id, error)


__all__ = ["BROWSER_TOOLS", "browser_tools", "find_command", "remove_conversation_browser", "tool_name"]
