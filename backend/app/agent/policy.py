"""What an agent may do without asking.

Per tool the user chooses "auto" or "ask" for the tools where that choice is safe to give; the choice is merged per
tool over global → environment → project. Allowlists let specific commands and web domains run without asking even
when their tool asks; the nearest level's list applies. Pushing always asks.

On top of that each conversation has an access level, chosen in the chat: default follows the above; full runs every
tool without asking, except pushing and a command with a secret; read_only refuses every tool that can change something.
"""

import re
import shlex
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlsplit

# Tools whose permission the user may change, with the label shown in Settings.
CONFIGURABLE_TOOLS: dict[str, str] = {
    "write_file": "Write files in the project",
    "edit_file": "Edit files in the project",
    "run_command": "Run commands in the project folder",
    "git_commit": "Commit in the project",
    "web_fetch": "Read web pages",
    "update_memory": "Add to the project memory",
}
PERMISSION_CHOICES = ("auto", "ask")
# What a conversation may do without asking: default follows the tool permissions in Settings; full asks only before
# pushing and before a command with a secret; read_only refuses whatever can change something.
AccessLevel = Literal["default", "full", "read_only"]
ACCESS_LEVELS: tuple[AccessLevel, ...] = ("default", "full", "read_only")
# Anything that could chain, redirect or substitute a second command: such a command is never allowlisted.
SHELL_SYNTAX = re.compile(r"[;&|`$<>(){}\\\n\r*?~!#]")
DOMAIN = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


@dataclass(frozen=True)
class ToolPolicy:
    permissions: dict[str, str] = field(default_factory=dict)
    commands: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    # The conversation's access level (ACCESS_LEVELS).
    access: AccessLevel = "default"
    # Tools allowed by name, whatever their arguments: an eval case's allowed_tools in its unattended run.
    tools: tuple[str, ...] = ()

    def permission_for(self, tool: str, default: str) -> str:
        return self.permissions.get(tool, default) if tool in CONFIGURABLE_TOOLS else default

    def allows(self, tool: str, arguments: dict[str, Any]) -> bool:
        """Whether an allowlist lets this call run without asking."""
        if tool in self.tools:
            return True
        if tool in ("run_command", "start_process"):
            return command_allowed(str(arguments.get("command") or ""), self.commands)
        if tool == "web_fetch":
            return url_allowed(str(arguments.get("url") or ""), self.domains)
        return False


def normalized_command(command: str) -> str:
    return " ".join(command.split())


def command_allowed(command: str, allowlist: tuple[str, ...]) -> bool:
    """The command is an allowlisted command, or one with only extra arguments after it, and nothing else."""
    if not command.strip() or SHELL_SYNTAX.search(command):
        return False
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    for entry in allowlist:
        allowed = shlex.split(entry)
        if allowed and words[: len(allowed)] == allowed:
            return True
    return False


def url_allowed(url: str, domains: tuple[str, ...]) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme not in ("http", "https") or not host or parts.username or parts.password:
        return False
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


def check_tool_permissions(value: Any) -> str | None:
    if not isinstance(value, dict):
        return "Give a permission per tool."
    for tool, permission in value.items():
        if tool not in CONFIGURABLE_TOOLS:
            return f"The permission of '{tool}' cannot be changed."
        if permission not in PERMISSION_CHOICES:
            return "A permission is either auto or ask."
    return None


def check_commands(value: Any) -> str | None:
    if not isinstance(value, list) or len(value) > 100:
        return "Give at most 100 commands."
    for entry in value:
        if not isinstance(entry, str) or not entry.strip() or len(entry) > 200:
            return "Each command is one line of at most 200 characters."
        if SHELL_SYNTAX.search(entry):
            return f"'{entry}' contains shell syntax; allowlist plain commands such as 'npm test'."
        try:
            shlex.split(entry)
        except ValueError:
            return f"'{entry}' has an unclosed quote."
    return None


def check_domains(value: Any) -> str | None:
    if not isinstance(value, list) or len(value) > 100:
        return "Give at most 100 domains."
    for entry in value:
        if not isinstance(entry, str) or not DOMAIN.match(entry.strip().lower()):
            return f"'{entry}' is not a domain such as docs.python.org."
    return None
