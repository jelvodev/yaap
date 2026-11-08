"""MCP tools for an agent run.

A server over stdio runs in the environment's runner, one per environment and configuration, in the
runner's /workspace; servers over HTTP are reached from YAAP as before. With the runner switched off (YAAP_RUNNER=off)
stdio servers run in YAAP's own process.
"""

import logging
from dataclasses import dataclass
from typing import Any

import anyio
from sqlalchemy.orm import Session

from app.agent.tool_catalog import ServerListing, lists_all
from app.core.settings import SETTING_DEFINITIONS, resolve_setting
from app.credentials.crypto import CredentialCipher
from app.mcp_client.config import SecretError, ServerConfig, parse_servers, with_secrets
from app.mcp_client.manager import McpManager, McpTool
from app.mcp_client.remote import PlaceFactory, RemotePlace
from app.runner.service import RUNNER_ROOT, RUNNERS, Runners
from app.secrets.service import placeholder_names, record_secret_uses, resolve_secrets

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ServerPlace:
    """Where the stdio servers of an environment run: the manager's scope, how to reach the runner, and the folder."""

    scope: str = ""
    place: PlaceFactory | None = None
    cwd: str | None = None


def server_place(environment_id: int, runners: Runners = RUNNERS) -> ServerPlace:
    if not runners.enabled:
        return ServerPlace()

    async def place() -> RemotePlace:
        return await runners.place(environment_id, "MCP servers in the runner")

    return ServerPlace(f"runner-e{environment_id}", place, RUNNER_ROOT)


def configured_servers(session: Session, environment_id: int, project_id: int | None) -> list[ServerConfig]:
    value = resolve_setting(session, SETTING_DEFINITIONS["mcp_servers"], environment_id, project_id).value
    return parse_servers(value)


def ready_servers(
    session: Session, cipher: CredentialCipher, environment_id: int, project_id: int | None
) -> tuple[list[ServerConfig], dict[str, str]]:
    """The configured servers with their secrets filled in, and per server that cannot start, why."""
    servers = configured_servers(session, environment_id, project_id)
    if not servers:
        return [], {}
    secrets = resolve_secrets(session, cipher, environment_id, project_id)
    ready, errors = [], {}
    for server in servers:
        try:
            ready.append(with_secrets(server, secrets))
            used = sorted({name for _, value in (*server.env, *server.headers) for name in placeholder_names(value)})
            if used:
                record_secret_uses(session, environment_id, project_id, used, f"MCP server {server.name}", None)
        except SecretError as error:
            errors[server.name] = str(error)
    return ready, errors


@dataclass(frozen=True)
class RunMcpTools:
    """The MCP tools of a run, as YAAP tools by the name the model sees, and its servers as the prompt lists them."""

    tools: dict[str, Any]
    servers: tuple[ServerListing, ...] = ()


def server_listings(servers: list[ServerConfig], tools: list[McpTool]) -> tuple[ServerListing, ...]:
    """The servers that offer tools, each fully listed or behind find_tools by its setting `list_all_tools`, or else
    by its number of tools."""
    listings = []
    for server in servers:
        names = tuple(tool.name for tool in tools if tool.server == server.name)
        if names:
            listed = lists_all(server.list_all_tools, len(names))
            listings.append(ServerListing(server.name, server.description, names, listed))
    return tuple(listings)


def load_run_tools(
    cipher: CredentialCipher, manager: McpManager, session: Session, environment_id: int, project_id: int | None
) -> RunMcpTools:
    """The MCP tools of the environment (and project) as YAAP tools, and its servers; called from a worker thread."""
    from app.agent.mcp_client_tools import mcp_tool

    servers, errors = ready_servers(session, cipher, environment_id, project_id)
    if not servers:
        for name, error in errors.items():
            logger.warning("MCP server %s not started: %s", name, error)
        return RunMcpTools({})
    where = server_place(environment_id)
    tools, failed = anyio.from_thread.run(manager.tools, servers, where.scope, where.cwd, where.place)
    for name, error in {**errors, **failed}.items():
        logger.warning("MCP server %s not available: %s", name, error)
    return RunMcpTools({tool.name: mcp_tool(manager, tool) for tool in tools}, server_listings(servers, tools))
