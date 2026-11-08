"""Tools of outside MCP servers as YAAP tools. They are a run's extra tools, not in the registry."""

import json
from typing import Any

from app.agent.guard import note_outside
from app.agent.runs import AgentRun
from app.agent.toolkit import Tool, ToolError, ToolOutput
from app.mcp_client.client import McpError


def mcp_tool(manager: Any, ref: Any) -> Tool:
    """An outside MCP server's tool as a YAAP tool: it asks unless set to run automatically, and what it returns
    counts as outside content."""

    async def handler(run: AgentRun, arguments: dict[str, Any]) -> str:
        try:
            result = await manager.call(ref, arguments)
        except McpError as error:
            raise ToolError(f"MCP server {ref.server}: {error}") from error
        note_outside(run, f"MCP server {ref.server}", result.text)
        if result.is_error:
            raise ToolError(result.text or f"{ref.tool} failed.")
        return ToolOutput.with_images(result.text or "(no output)", list(result.images))

    def describe(arguments: dict[str, Any]) -> str:
        shown = json.dumps(arguments, ensure_ascii=False)
        return f"{ref.server}: {ref.tool} {shown[:200]}{'…' if len(shown) > 200 else ''}"

    return Tool(
        ref.name,
        ref.description,
        ref.input_schema,
        "auto" if ref.auto else "ask",
        handler,
        describe=describe,
        scope="always",
        read_only=ref.read_only,
    )
