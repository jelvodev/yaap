"""The command tool: run_command, with its environment, secrets, live output and shell sessions."""

import re
import time
from typing import Any

from app.agent.outputs import STORE_ABOVE, full_output_note
from app.agent.runs import AgentRun
from app.agent.shells import CONTAINER_PLACE, ShellSessions, check_name
from app.agent.test_output import essentials, recognise_runner
from app.agent.tool_context import commands_remote, executor, on_target
from app.agent.toolkit import STRING, Tool, ToolError, schema, text_argument
from app.checkpoints.service import call_key
from app.core.reply_event_models import ToolOutputEvent
from app.core.subprocess_env import safe_subprocess_env
from app.secrets.service import SecretError, mask, placeholder_names, substitute
from app.targets.base import CommandResult
from app.targets.container import require_bash
from app.targets.sessions import ShellState

# Commands run without a terminal: pagers, prompts and colours are switched off unless set.
NON_INTERACTIVE = {
    "PAGER": "cat",
    "GIT_PAGER": "cat",
    "GIT_TERMINAL_PROMPT": "0",
    "DEBIAN_FRONTEND": "noninteractive",
    "CI": "1",
    "NO_COLOR": "1",
}
DEFAULT_SILENCE = 60
WAITS_FOR_INPUT = re.compile(r"(\?|:|\[y/n\]|\(y/n\)|password|passphrase|press (any|enter|return))\s*$", re.IGNORECASE)
LIVE_INTERVAL = 0.5


def command_environment() -> dict[str, str]:
    """YAAP's own variables and credentials never reach commands the agent runs."""
    return {**NON_INTERACTIVE, **safe_subprocess_env()}


class LiveOutput:
    """Sends a command's output to the chat while it runs, at most every LIVE_INTERVAL seconds."""

    def __init__(self, run: AgentRun, key: str) -> None:
        self.run, self.key = run, key
        self.pending = ""
        self.sent_at = 0.0

    def __call__(self, text: str) -> None:
        self.pending += text
        if time.monotonic() - self.sent_at >= LIVE_INTERVAL:
            self.flush()

    def flush(self) -> None:
        if not self.pending:
            return
        text = mask(self.pending, self.run.secrets) if self.run.secrets else self.pending
        self.run.events.put_nowait(ToolOutputEvent(call_key=self.key, text=text[-20_000:]))
        self.pending = ""
        self.sent_at = time.monotonic()


def stopped_note(result: CommandResult, timeout: int, silence: int) -> str:
    if result.stopped == "silence":
        last = next((line.strip() for line in reversed(result.output.splitlines()) if line.strip()), "")
        reason = f"Stopped after {silence} seconds without new output."
        if WAITS_FOR_INPUT.search(last):
            reason += (
                " It probably waits for input, which commands here cannot get: run it with a flag that answers for it "
                "(such as --yes or --non-interactive), or give the input another way."
            )
    else:
        reason = f"Stopped after {timeout} seconds."
    return reason + " For a command that keeps running, such as a server or a watcher, use start_process."


def with_secrets(run: AgentRun, command: str) -> tuple[str, dict[str, str]]:
    """The command with its secret placeholders as variable references, and the environment with their values."""
    names = placeholder_names(command)
    powershell = on_target(run) and getattr(run.target.settings, "shell", "") in ("pwsh", "powershell")
    try:
        command, variables = substitute(command, run.secrets, powershell)
    except SecretError as error:
        raise ToolError(str(error)) from error
    if names and run.on_secret_use is not None:
        run.on_secret_use(names, "Command in a conversation")
    return command, {**command_environment(), **variables}


async def run_command(run: AgentRun, arguments: dict[str, Any]) -> str:
    command, environment = with_secrets(run, text_argument(arguments, "command"))
    timeout = min(max(int(arguments.get("timeout_seconds") or 120), 1), 600)
    silence = min(max(int(arguments.get("silence_seconds") or DEFAULT_SILENCE), 5), 600)
    session = shell_state(run, text_argument(arguments, "session", required=False).strip(), save=True)
    live = LiveOutput(run, call_key("run_command", arguments))
    result = await executor(run).run(command, environment, timeout, session, silence=silence, on_output=live)
    live.flush()
    if result.timed_out:
        return f"{stopped_note(result, timeout, silence)}\nOutput so far:\n{result.output}".rstrip()
    shortened = shortened_test_output(run, command, result)
    return shortened or f"Exit code {result.exit_code}\n{result.output}".rstrip()


def shortened_test_output(run: AgentRun, command: str, result: CommandResult) -> str | None:
    """A long output of a known test runner as its failures and summary, with the whole output stored (secrets
    masked, as the gate would); None when the output stays as it is."""
    output = result.output.rstrip()
    outputs = run.outputs
    if not (run.state.context_savings and outputs and len(output) > STORE_ABOVE and "read_output" in run.tool_names):
        return None
    runner = recognise_runner(command)
    kept = essentials(runner, output, result.exit_code) if runner else None
    if kept is None:
        return None
    whole = f"Exit code {result.exit_code}\n{output}"
    whole = mask(whole, run.secrets) if run.secrets else whole
    kept = mask(kept, run.secrets) if run.secrets else kept
    return (
        f"Exit code {result.exit_code}\n[Test output shortened to the failures and the summary.]\n{kept}\n\n"
        + full_output_note(whole, outputs.save(whole))
    )


def shell_state(run: AgentRun, name: str, save: bool) -> ShellState | None:
    """The conversation's shell session `name`, or None without a name. run_command (save) starts the session when
    it does not exist; start_process only uses one that does."""
    if not name:
        return None
    check_name(name)
    shells: ShellSessions | None = run.shells
    if shells is None:
        raise ToolError("Shell sessions are not available in this run. Run the command without a session.")
    remote = commands_remote(run)
    if not remote:
        require_bash()
    place = run.target.key if remote else CONTAINER_PLACE
    row = shells.open(run.conversation_id, name, place, create=save)
    if row is None:
        raise ToolError(f"There is no session '{name}' in this conversation; start it with run_command first.")
    return shells.state(row, save)


COMMAND_TOOLS = [
    Tool(
        "run_command",
        "Run a shell command in the project folder (tests, a build) and wait for it; timeout_seconds defaults to "
        "120. With a session name, the working folder and exported variables (an activated virtualenv) carry over "
        "to later calls with that session, until two idle hours. For a server that keeps running, use start_process.",
        schema(
            {
                "command": STRING,
                "timeout_seconds": {"type": "integer"},
                "silence_seconds": {
                    "type": "integer",
                    "description": "Stop after this long without output; default 60.",
                },
                "session": STRING,
            },
            ["command"],
        ),
        "ask",
        run_command,
        describe=lambda arguments: f"Run: {arguments.get('command', '')}",
    ),
]
