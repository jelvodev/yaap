"""An agent run's tools as a ToolBox, for adapters that run the tool loop in-process."""

from typing import Any

from app.agent.gate import call_tool
from app.agent.runs import AgentRun, RunRegistry
from app.agent.tools import run_tools
from app.providers.base import ToolDefinition, ToolResult


class RunToolBox:
    """Calls go through the same permission gate and approval cards as calls over MCP."""

    def __init__(self, registry: RunRegistry, run: AgentRun) -> None:
        self.registry = registry
        self.run = run

    @property
    def definitions(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(tool.name, tool.description, tool.input_schema, tool.read_only) for tool in run_tools(self.run)
        ]

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        if self.registry.find(self.run.token) is None:
            return ToolResult("The run has ended; the tool was not run.", is_error=True)
        output, is_error = await call_tool(self.registry, self.run, name, arguments)
        return ToolResult(str(output), is_error, tuple(getattr(output, "images", ())))
