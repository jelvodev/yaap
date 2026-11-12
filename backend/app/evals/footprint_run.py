"""Measuring the token footprint: YAAP runs in this process on a throwaway data folder, and a recording
model provider keeps what each reply's first model request holds.

The request is the one the provider adapters get, built by YAAP's own code: the reply service, the system parts,
the run's tools (YAAP's, the browser's and an outside MCP server's) and the gate, so a change to any of them shows up
in the numbers. Nothing goes over the network:

- the browser is a stand-in that lists Playwright MCP's tools from `evals/baseline/footprint/playwright_tools.json`;
- the outside MCP server is the baseline's issue tracker (`evals/baseline/mcp/tracker_server.py`), over stdio;
- web search is configured with an address that is never called, as a SearXNG install would be;
- the project is the baseline project, built with `app.evals.baseline.build_project`;
- the provider is a stand-in that calls a fixed sequence of tools and records their results.

Commands run in this process (YAAP_RUNNER=off), and the master key and password are generated for the run and gone
with its data folder. An empty Blueprint is used, so the numbers are YAAP's own overhead, not a user's standards.
"""

import os
import secrets
import sys
import tempfile
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.credentials.types import CredentialTestResult, CredentialType
from app.evals.baseline import BASELINE_DIR, build_project
from app.evals.footprint import Footprint, Part, Situation, request_parts, result_part
from app.evals.workspace import remove_folder
from app.git.place import git_place
from app.main import create_app
from app.providers.base import (
    AgentEvent,
    AgentRequest,
    Completed,
    TextDelta,
    TokenUsage,
    ToolCallFinished,
    ToolCallStarted,
)
from app.providers.catalog import ANTHROPIC_MODELS
from app.providers.registry import ProviderRegistry, ProviderSpec
from app.websearch.service import SearxngSearch

MODEL = "claude-opus-5-5"
API = "/api/v1"
UNUSED_SEARCH_URL = "http://127.0.0.1:9"
STAND_IN_KEY = "footprint-stand-in-not-a-key"
LARGE_FILE = "stockroom/inventory.py"
NOTES = (
    "Browser tool definitions are a hand-written stand-in for Playwright MCP 0.0.83's (playwright_tools.json).",
    "The outside MCP server is the baseline's issue tracker (22 tools).",
    "The Blueprint is empty: the system part is YAAP's own; a user's standards and skills come on top.",
    "Command output depends on the platform's line endings; compare footprints made on the same platform.",
)

# One reply each: (tool, arguments) calls the stand-in model makes before it answers.
ToolCalls = tuple[tuple[str, dict[str, Any]], ...]


def project_calls(python: str) -> ToolCalls:
    """The first calls of a typical task: look around, search, read a large file, run a command with long output."""
    return (
        ("list_files", {"path": ".", "recursive": True}),
        ("search", {"pattern": "def expire_reservations|def release", "path": "stockroom"}),
        ("read_file", {"path": LARGE_FILE}),
        ("run_command", {"command": f'"{python}" -m stockroom.cli report'}),
    )


@dataclass
class Recorded:
    request: AgentRequest
    results: list[tuple[str, str]] = field(default_factory=list)


class RecordingProvider:
    """A model provider that keeps each request, calls the scripted tools through the request's tool box (the real
    gate) and records their results, then answers with a short text."""

    def __init__(self, script: list[ToolCalls], recorded: list[Recorded]) -> None:
        self.script = script
        self.recorded = recorded

    async def stream(self, request: AgentRequest) -> AsyncIterator[AgentEvent]:
        record = Recorded(request)
        self.recorded.append(record)
        calls = self.script.pop(0) if self.script else ()
        for index, (name, arguments) in enumerate(calls):
            if request.tool_box is None:
                break
            yield ToolCallStarted(f"footprint-{index}", name, arguments)
            result = await request.tool_box.call(name, arguments)
            record.results.append((name, result.text))
            yield ToolCallFinished(f"footprint-{index}", result.text, result.is_error)
        yield TextDelta("Done.")
        yield Completed(stop_reason="end_turn", served_by_model=request.model, usage=TokenUsage(), provider_raw=[])

    async def close(self) -> None:
        return None


async def accept_any_key(_value: str) -> CredentialTestResult:
    return CredentialTestResult(True, "Stand-in key for the token footprint.")


@contextmanager
def environment(values: dict[str, str]) -> Iterator[None]:
    """Set YAAP's start variables for the measurement and put the old ones back afterwards."""
    old = {name: os.environ.get(name) for name in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for name, value in old.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def recording_app(script: list[ToolCalls], recorded: list[Recorded], python: str, source: Path) -> FastAPI:
    credential = CredentialType("anthropic_api_key", "Anthropic API key", "Stand-in.", accept_any_key)
    spec = ProviderSpec(
        "anthropic", "Anthropic", "anthropic_api_key", lambda _key: RecordingProvider(script, recorded),
        connection="api_key", runs_agents=True,
    )
    providers = ProviderRegistry([spec], ANTHROPIC_MODELS)
    app = create_app(credential_types={credential.kind: credential}, providers=providers)
    app.state.browser_command = [python, str(source / "footprint" / "stand_in_server.py")]
    app.state.web_search = SearxngSearch(UNUSED_SEARCH_URL)
    for flag in ("utility_titles", "memory_suggestions", "memory_tidy", "scheduler", "background_indexing"):
        setattr(app.state, flag, False)
    app.state.load_embedder = lambda: None
    app.state.model_fetch = no_download
    return app


def no_download(*_args: object) -> None:
    """The run measures YAAP without the network: no model is fetched."""
    raise OSError("No network in the footprint run.")


def sign_in(client: TestClient) -> None:
    """A password made for this run only, in its throwaway data folder."""
    response = client.post(f"{API}/auth/setup", json={"password": secrets.token_urlsafe(24)})
    response.raise_for_status()
    client.headers["X-CSRF-Token"] = client.cookies["yaap_csrf"]


def configure(client: TestClient, python: str, source: Path) -> None:
    client.put(f"{API}/settings/anthropic_connection", json={"value": "api_key"}).raise_for_status()
    client.put(f"{API}/environments/1/credentials/anthropic_api_key", json={"value": STAND_IN_KEY}).raise_for_status()
    tracker = {
        "transport": "stdio",
        "command": python,
        "args": [str(source / "mcp" / "tracker_server.py")],
        "description": "The Stockroom issue tracker",
    }
    client.put(f"{API}/environments/1/settings/mcp_servers", json={"value": {"tracker": tracker}}).raise_for_status()


def new_conversation(client: TestClient, project_id: int | None = None) -> int:
    body: dict[str, Any] = {"model": MODEL, "effort": "medium", "project_id": project_id, "access": "full"}
    response = client.post(f"{API}/environments/1/conversations", json=body)
    response.raise_for_status()
    return int(response.json()["id"])


def send(client: TestClient, conversation_id: int, text: str) -> None:
    client.post(f"{API}/conversations/{conversation_id}/messages", json={"text": text}).raise_for_status()


def baseline_project(client: TestClient, workspace_dir: Path, source: Path) -> int:
    """A new project whose (empty) folder becomes the baseline project."""
    response = client.post(f"{API}/environments/1/projects", json={"name": "Stockroom"})
    response.raise_for_status()
    project = response.json()
    build_project(git_place(int(project["environment_id"]), workspace_dir / project["folder"]), source)
    return int(project["id"])


def situation(name: str, description: str, record: Recorded) -> Situation:
    parts, tools = request_parts(record.request)
    results: list[Part] = [result_part(index, tool, text) for index, (tool, text) in enumerate(record.results, 1)]
    return Situation(name, description, (*parts, *results), tools)


def measure_footprint(python: str = sys.executable, source: Path = BASELINE_DIR) -> Footprint:
    """The footprint of three situations: a plain chat, a project conversation with its first tool results, and the
    first turn of a goal. The browser and an outside MCP server are configured in all of them."""
    script: list[ToolCalls] = [(), project_calls(python), (("finish_goal", {"status": "done", "summary": "Done."}),)]
    recorded: list[Recorded] = []
    data_dir = Path(tempfile.mkdtemp(prefix="yaap-footprint-"))
    variables = {"YAAP_DATA_DIR": str(data_dir), "YAAP_MASTER_KEY": Fernet.generate_key().decode()}
    variables |= {"YAAP_RUNNER": "off", "YAAP_ALLOWED_HOSTS": "testserver"}
    try:
        with environment(variables):
            app = recording_app(script, recorded, python, source)
            with TestClient(app) as client:
                sign_in(client)
                configure(client, python, source)
                send(client, new_conversation(client), "What is the difference between a list and a tuple in Python?")
                project_id = baseline_project(client, app.state.workspace_dir, source)
                send(client, new_conversation(client, project_id), "Where are expired reservations released?")
                send(client, new_conversation(client, project_id), "/goal Add a low-stock command to the CLI.")
    finally:
        remove_folder(data_dir)
    if len(recorded) != 3:
        raise RuntimeError(f"Expected three model requests, got {len(recorded)}.")
    return Footprint(
        (
            situation("plain_chat", "a conversation outside a project, first request", recorded[0]),
            situation("project", "a conversation in the baseline project, first request and tool results", recorded[1]),
            situation("goal_turn", "the first turn of a goal in the baseline project", recorded[2]),
        ),
        NOTES,
    )
