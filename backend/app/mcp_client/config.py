"""MCP server configuration.

The setting `mcp_servers` holds the servers per environment; a project adds servers or overrides one by name (the
setting merges key by key). Values in `env` and `headers` may be `{{secret:NAME}}`, filled in when the server starts.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from app.secrets.service import SecretError, fill_in

Transport = Literal["stdio", "http", "sse"]
NAME = re.compile(r"^[A-Za-z0-9_-]{1,30}$")
MAX_SERVERS = 20
DEFAULT_START_TIMEOUT = 10
DEFAULT_CALL_TIMEOUT = 120
KEYS = {
    "transport",
    "command",
    "args",
    "env",
    "url",
    "headers",
    "start_timeout",
    "call_timeout",
    "disabled_tools",
    "auto_tools",
    "enabled",
    "description",
    "list_all_tools",
}


@dataclass(frozen=True)
class ServerConfig:
    name: str
    transport: Transport
    command: str = ""
    args: tuple[str, ...] = ()
    env: tuple[tuple[str, str], ...] = ()
    url: str = ""
    headers: tuple[tuple[str, str], ...] = ()
    start_timeout: float = DEFAULT_START_TIMEOUT
    call_timeout: float = DEFAULT_CALL_TIMEOUT
    disabled_tools: frozenset[str] = frozenset()
    auto_tools: frozenset[str] = frozenset()
    # What the server is for, shown to the agent in the list of servers in the prompt.
    description: str = ""
    # Offer every tool from the start (True) or only through find_tools (False); None decides by the number of tools.
    list_all_tools: bool | None = None
    # Filled in by YAAP (the agent's browser): which tools to offer, and per-conversation settings.
    only_tools: frozenset[str] | None = None
    extra: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    def fingerprint(self) -> str:
        """Same configuration and same secret values: the same running server can serve it."""
        data = [self.transport, self.command, self.args, self.env, self.url, self.headers]
        return hashlib.sha256(json.dumps(data, default=list).encode()).hexdigest()[:16]


def string_map(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, dict) or not all(isinstance(key, str) and isinstance(item, str) for key, item in value.items()):
        return f"{label} is an object of text values."
    return None


def check_server(name: str, value: Any) -> str | None:
    if not NAME.match(name):
        return f"Server name {name!r}: use letters, digits, - and _, at most 30 characters."
    if value is None:
        return None  # A project switches an inherited server off by setting it to null.
    if not isinstance(value, dict):
        return f"Server {name}: use an object."
    unknown = set(value) - KEYS
    if unknown:
        return f"Server {name}: unknown keys {', '.join(sorted(unknown))}."
    transport = value.get("transport", "stdio")
    if transport not in ("stdio", "http", "sse"):
        return f"Server {name}: transport is stdio, http or sse."
    if transport == "stdio" and not (isinstance(value.get("command"), str) and value["command"].strip()):
        return f"Server {name}: a stdio server needs a command."
    if transport != "stdio" and not (isinstance(value.get("url"), str) and re.match(r"^https?://", value["url"])):
        return f"Server {name}: an {transport} server needs an http(s) url."
    args = value.get("args", [])
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        return f"Server {name}: args is a list of text."
    for key in ("env", "headers"):
        problem = string_map(value.get(key), f"Server {name}: {key}")
        if problem:
            return problem
    for key in ("start_timeout", "call_timeout"):
        if key in value and not (isinstance(value[key], (int, float)) and 1 <= value[key] <= 3600):
            return f"Server {name}: {key} is a number of seconds from 1 to 3600."
    for key in ("disabled_tools", "auto_tools"):
        items = value.get(key, [])
        if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
            return f"Server {name}: {key} is a list of tool names."
    for key in ("enabled", "list_all_tools"):
        if key in value and not isinstance(value[key], bool):
            return f"Server {name}: {key} is true or false."
    return None


def validate_servers(value: Any, _providers: Any = None) -> str | None:
    if not isinstance(value, dict):
        return "Use an object with a server per name."
    if len(value) > MAX_SERVERS:
        return f"At most {MAX_SERVERS} servers."
    for name, server in value.items():
        problem = check_server(str(name), server)
        if problem:
            return problem
    return None


def parse_servers(value: Any) -> list[ServerConfig]:
    """The enabled servers of a resolved setting value."""
    if not isinstance(value, dict) or validate_servers(value):
        return []
    servers = []
    for name, item in sorted(value.items()):
        if item is None or item.get("enabled", True) is False:
            continue
        servers.append(
            ServerConfig(
                name=name,
                transport=item.get("transport", "stdio"),
                command=item.get("command", ""),
                args=tuple(item.get("args", [])),
                env=tuple(sorted((item.get("env") or {}).items())),
                url=item.get("url", ""),
                headers=tuple(sorted((item.get("headers") or {}).items())),
                start_timeout=float(item.get("start_timeout", DEFAULT_START_TIMEOUT)),
                call_timeout=float(item.get("call_timeout", DEFAULT_CALL_TIMEOUT)),
                disabled_tools=frozenset(item.get("disabled_tools", [])),
                auto_tools=frozenset(item.get("auto_tools", [])),
                description=str(item.get("description", "")),
                list_all_tools=item.get("list_all_tools"),
            )
        )
    return servers


def with_secrets(server: ServerConfig, secrets: dict[str, str]) -> ServerConfig:
    """The server with {{secret:NAME}} in env and headers filled in. Raises SecretError for an unknown secret."""

    def fill(pairs: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
        return tuple((key, fill_in(value, secrets)) for key, value in pairs)

    return ServerConfig(
        **{**server.__dict__, "env": fill(server.env), "headers": fill(server.headers)}  # type: ignore[arg-type]
    )


__all__ = ["SecretError", "ServerConfig", "parse_servers", "validate_servers", "with_secrets"]
