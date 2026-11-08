"""The background process tools: start, read, stop and list processes that keep running after a reply.

The processes themselves are kept by `ProcessManager` (app.agent.processes); these tools are the model's handle on it.
"""

import asyncio
import time
from typing import Any

from app.agent.command_tools import shell_state, with_secrets
from app.agent.processes import ProcessManager
from app.agent.runs import AgentRun
from app.agent.tool_context import executor
from app.agent.toolkit import STRING, Tool, ToolError, schema, text_argument


def process_manager(run: AgentRun) -> ProcessManager:
    if run.processes is None:
        raise ToolError("Background processes are not available in this run.")
    return run.processes


def process_id(arguments: dict[str, Any]) -> int:
    value = arguments.get("id")
    if isinstance(value, bool) or not isinstance(value, int | str) or not str(value).isdigit():
        raise ToolError("'id' must be the number of a process; list_processes shows them.")
    return int(value)


async def start_process(run: AgentRun, arguments: dict[str, Any]) -> str:
    command, environment = with_secrets(run, text_argument(arguments, "command"))
    port = arguments.get("port")
    if port is not None and (isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535):
        raise ToolError("'port' must be a port number.")
    session = shell_state(run, text_argument(arguments, "session", required=False).strip(), save=False)
    manager = process_manager(run)
    managed = await manager.start(
        command=command,
        executor=executor(run),
        env=environment,
        environment_id=run.environment_id,
        project_id=run.project_id,
        conversation_id=run.conversation_id,
        port=port,
        session=session,
        target=run.target,
    )
    # A moment for the first output, so a command that fails at once shows why.
    try:
        await asyncio.wait_for(managed.done.wait(), timeout=1.5)
    except TimeoutError:
        pass
    return (
        f"Started process {managed.id}. Read its output with read_process_output and stop it with stop_process; "
        f"it keeps running after this reply.\n{manager.read(managed)}"
    )


async def read_process_output(run: AgentRun, arguments: dict[str, Any]) -> str:
    manager = process_manager(run)
    return manager.read(manager.find(process_id(arguments), run.project_id))


async def stop_process(run: AgentRun, arguments: dict[str, Any]) -> str:
    manager = process_manager(run)
    managed = await manager.stop(manager.find(process_id(arguments), run.project_id), "stopped by the agent")
    return f"{managed.describe()}\nLast output:\n{managed.tail(1500) or '(none)'}"


async def list_processes(run: AgentRun, _arguments: dict[str, Any]) -> str:
    manager = process_manager(run)
    found = [managed for managed in manager.processes.values() if managed.project_id == run.project_id]
    for managed in found:
        managed.last_touched = time.monotonic()
    return "\n".join(managed.describe() for managed in found[-30:]) or "No processes in this project."


PROCESS_TOOLS = [
    Tool(
        "start_process",
        "Start a command that keeps running in the background, such as a dev server or a watcher, in the project "
        "folder. Returns its id and first output; it keeps running after this reply. port is where a web app "
        "listens: YAAP then shows the app in the preview panel beside the chat, where the user can open it. session "
        "uses a run_command session's folder and variables. The user approves each start.",
        schema(
            {"command": STRING, "port": {"type": "integer"}, "session": STRING},
            ["command"],
        ),
        "ask",
        start_process,
        describe=lambda arguments: f"Start in the background: {arguments.get('command', '')}",
    ),
    Tool(
        "read_process_output",
        "Read the output a background process wrote since the last read, with its state.",
        schema({"id": {"type": "integer"}}, ["id"]),
        "auto",
        read_process_output,
        read_only=True,
    ),
    Tool(
        "stop_process",
        "Stop a background process and what it started.",
        schema({"id": {"type": "integer"}}, ["id"]),
        "auto",
        stop_process,
    ),
    Tool(
        "list_processes",
        "List this project's background processes with their state.",
        schema({}, []),
        "auto",
        list_processes,
        read_only=True,
    ),
]
