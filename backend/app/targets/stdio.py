"""Starting a long-running program over SSH with its standard input and output as the conversation.

MCP servers (app/mcp_client/remote.py) and language servers (app/lsp/) run in the runner or a sandbox this way. YAAP
opens an SSH channel with one fixed launcher; the first line it sends on that channel is a script, base64 so it is one
line, that sets the program's environment, changes to its folder and `exec`s it. Every following byte belongs to the
program. bash reads that first line byte by byte from a pipe, so nothing of the conversation is taken from the
program, and no value appears on a command line or in the SSH environment.
"""

import base64
import re
from collections.abc import Sequence

from app.targets.base import TargetError
from app.targets.ssh import shell_quote

# Reads the script line and runs it; the script ends in `exec`, so the program keeps the channel's input and output.
LAUNCHER = "bash -c 'IFS= read -r line && eval \"$(printf %s \"$line\" | base64 -d)\"'"
VARIABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def exec_script(argv: Sequence[str], env: Sequence[tuple[str, str]] = (), cwd: str | None = None) -> str:
    """The script the launcher runs: the variables, the folder, then the program in place of the shell."""
    lines = []
    for name, value in env:
        if not VARIABLE.match(name):
            raise TargetError(f"{name} is not a valid environment variable name.")
        lines.append(f"export {name}={shell_quote('linux', value)}")
    if cwd:
        lines.append(f"cd {shell_quote('linux', cwd)} || exit 1")
    lines.append("exec " + " ".join(shell_quote("linux", part) for part in argv))
    return "\n".join(lines) + "\n"


def exec_line(script: str) -> bytes:
    """The first line on the channel: the script, base64 so it is one line."""
    return base64.b64encode(script.encode("utf-8")) + b"\n"
