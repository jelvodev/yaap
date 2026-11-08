"""The stored-output tool: read_output pages through a long tool output that was shortened.

How outputs are stored and shortened lives in app.agent.outputs.
"""

import asyncio
from typing import Any

from app.agent.outputs import read_range, search_lines
from app.agent.runs import AgentRun
from app.agent.toolkit import STRING, Tool, ToolError, schema


async def read_output(run: AgentRun, arguments: dict[str, Any]) -> str:
    if run.outputs is None:
        raise ToolError("There are no stored outputs in this conversation.")
    try:
        number = int(arguments.get("output") or 0)
        start = int(arguments.get("start_line") or 1)
        end = int(arguments["end_line"]) if arguments.get("end_line") else None
    except (TypeError, ValueError) as error:
        raise ToolError("output, start_line and end_line must be numbers.") from error
    text = await asyncio.to_thread(run.outputs.read, number)
    if text is None:
        raise ToolError(f"There is no output {number} in this conversation.")
    search = str(arguments.get("search") or "").strip()
    return search_lines(text, search) if search else read_range(text, start, end)


OUTPUT_TOOLS = [
    Tool(
        "read_output",
        "Read a part of a long tool output that was shortened: a range of lines, or the lines that match a search "
        "(a regular expression, case-insensitive). The shortened output names its number.",
        schema(
            {
                "output": {"type": "integer"},
                "start_line": {"type": "integer"},
                "end_line": {"type": "integer"},
                "search": STRING,
            },
            ["output"],
        ),
        "auto",
        read_output,
        read_only=True,
        scope="always",
    ),
]
