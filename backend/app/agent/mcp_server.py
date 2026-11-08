"""YAAP's tools as an MCP server over Streamable HTTP, answering every request with plain JSON.

Only requests with the bearer token of a running agent run are served; that token decides the project folder.
A browser request from another site's page is refused in front of every route (app/auth/boundary.py), so a web page
cannot reach this endpoint through the user's browser.
"""

import json
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import JSONResponse

from app.agent.gate import call_tool
from app.agent.runs import AgentRun, RunRegistry
from app.agent.tools import mcp_listed_tools

router = APIRouter(include_in_schema=False)

DEFAULT_PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "yaap"
INSTRUCTIONS = (
    "YAAP's tools work inside the current project folder and on the conversation's attachments. Project paths are "
    "relative to the project. Some tools wait for the user's approval in YAAP; when the user declines, do not retry "
    "the same action."
)


def rpc_result(request_id: Any, result: dict[str, Any]) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result})


def rpc_error(request_id: Any, code: int, message: str, http_status: int = status.HTTP_200_OK) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}, http_status)


@router.post("/mcp")
async def mcp_endpoint(request: Request) -> Response:
    registry: RunRegistry = request.app.state.runs
    authorization = request.headers.get("authorization", "")
    token = authorization.removeprefix("Bearer ").strip() if authorization.startswith("Bearer ") else ""
    run = registry.find(token) if token else None
    if run is None:
        return Response(status_code=status.HTTP_401_UNAUTHORIZED)
    return await serve_message(request, registry, run)


# Called after each tools/call with the tool's name, arguments, output and whether it failed (outside clients log it).
CallObserver = Callable[[str, dict[str, Any], str, bool], Awaitable[None]]


async def serve_message(
    request: Request,
    registry: RunRegistry,
    run: AgentRun,
    instructions: str = INSTRUCTIONS,
    on_call: CallObserver | None = None,
) -> Response:
    """Answer one JSON-RPC message of the Streamable HTTP transport for a run whose caller is already checked."""
    try:
        message = json.loads(await request.body())
    except json.JSONDecodeError:
        return rpc_error(None, -32700, "Parse error.", status.HTTP_400_BAD_REQUEST)
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return rpc_error(None, -32600, "Invalid request.", status.HTTP_400_BAD_REQUEST)
    if "id" not in message or "method" not in message:
        # A notification or a response from the client: accepted, nothing to answer.
        return Response(status_code=status.HTTP_202_ACCEPTED)

    request_id, method = message["id"], message["method"]
    params = message.get("params") or {}
    if method == "initialize":
        return rpc_result(
            request_id,
            {
                "protocolVersion": params.get("protocolVersion") or DEFAULT_PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": "0.1.0"},
                "instructions": instructions,
            },
        )
    if method == "ping":
        return rpc_result(request_id, {})
    if method == "tools/list":
        return rpc_result(
            request_id,
            {
                "tools": [
                    {"name": tool.name, "description": tool.description, "inputSchema": tool.input_schema}
                    for tool in mcp_listed_tools(run)
                ]
            },
        )
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str) or not isinstance(arguments, dict):
            return rpc_error(request_id, -32602, "tools/call needs a name and an arguments object.")
        output, is_error = await call_tool(registry, run, name, arguments)
        if on_call is not None:
            await on_call(name, arguments, str(output), is_error)
        content: list[dict[str, Any]] = [{"type": "text", "text": str(output)}]
        content += [
            {"type": "image", "data": data, "mimeType": media_type} for media_type, data in getattr(output, "images", ())
        ]
        return rpc_result(request_id, {"content": content, "isError": is_error})
    return rpc_error(request_id, -32601, f"Method not found: {method}")


@router.get("/mcp")
@router.delete("/mcp")
async def mcp_not_streaming(request: Request) -> Response:
    """No server-initiated stream and no explicit session end: both are optional in the transport. Without a valid
    token the answer is 401, as for every other route."""
    registry: RunRegistry = request.app.state.runs
    authorization = request.headers.get("authorization", "")
    token = authorization.removeprefix("Bearer ").strip() if authorization.startswith("Bearer ") else ""
    if not token or registry.find(token) is None:
        return Response(status_code=status.HTTP_401_UNAUTHORIZED)
    return Response(status_code=status.HTTP_405_METHOD_NOT_ALLOWED)
