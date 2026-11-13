"""MCP servers in Settings: their status, tools and log, and a test that (re)starts one."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from app.api.dependencies import EnvironmentDependency, SessionDependency
from app.mcp_client.client import McpError
from app.mcp_client.config import ServerConfig
from app.mcp_client.manager import Connection
from app.mcp_client.runs import configured_servers, ready_servers, server_place
from app.storage.models import Project

router = APIRouter(tags=["mcp"])


class McpToolOut(BaseModel):
    name: str
    # The name the model sees: <server>__<tool>.
    model_name: str
    description: str
    disabled: bool
    auto: bool


class McpServerOut(BaseModel):
    name: str
    transport: Literal["stdio", "http", "sse"]
    target: str
    state: Literal["running", "stopped", "failed"]
    error: str | None
    tools: list[McpToolOut]
    log: list[str]


def server_out(server: ServerConfig, connection: Connection | None, error: str | None = None) -> McpServerOut:
    from app.mcp_client.manager import tool_name

    raw = connection.raw_tools if connection else []
    failure = error or (connection.error if connection else None)
    state = "running" if connection and connection.running else ("failed" if failure else "stopped")
    return McpServerOut(
        name=server.name,
        transport=server.transport,
        target=server.url or " ".join([server.command, *server.args]),
        state=state,
        error=failure,
        tools=[
            McpToolOut(
                name=item["name"],
                model_name=tool_name(server.name, item["name"]),
                description=str(item.get("description") or "")[:300],
                disabled=item["name"] in server.disabled_tools,
                auto=item["name"] in server.auto_tools,
            )
            for item in raw
        ],
        log=connection.log()[-50:] if connection else [],
    )


def check_project(session: SessionDependency, environment_id: int, project_id: int | None) -> None:
    if project_id is None:
        return
    project = session.get(Project, project_id)
    if project is None or project.environment_id != environment_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found.")


@router.get("/environments/{environment_id}/mcp", response_model=list[McpServerOut])
def list_mcp_servers(
    environment: EnvironmentDependency, session: SessionDependency, request: Request, project_id: int | None = None
) -> list[McpServerOut]:
    check_project(session, environment.id, project_id)
    servers, errors = ready_servers(session, request.app.state.cipher, environment.id, project_id)
    ready = {server.name: server for server in servers}
    where = server_place(environment.id)
    result = []
    for server in configured_servers(session, environment.id, project_id):
        filled = ready.get(server.name)
        connection = request.app.state.mcp.find(filled, where.scope) if filled else None
        result.append(server_out(filled or server, connection, errors.get(server.name)))
    return result


@router.post("/environments/{environment_id}/mcp/{name}/test", response_model=McpServerOut)
async def test_mcp_server(
    name: str, environment: EnvironmentDependency, session: SessionDependency, request: Request, project_id: int | None = None
) -> McpServerOut:
    """Start the server again and read its tools; a stdio server starts in the environment's runner."""
    check_project(session, environment.id, project_id)
    servers, errors = ready_servers(session, request.app.state.cipher, environment.id, project_id)
    session.close()
    if name in errors:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, errors[name])
    server = next((item for item in servers if item.name == name), None)
    if server is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No enabled server with this name.")
    where = server_place(environment.id)
    try:
        connection = await request.app.state.mcp.restart(server, where.scope, where.cwd, where.place)
    except McpError as error:
        return server_out(server, request.app.state.mcp.find(server, where.scope), str(error))
    return server_out(server, connection)
