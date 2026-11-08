"""Remote A2A agents per environment: the setting `a2a_agents`.

The value is an object with an agent per name: its url (the agent card, or the address the card is found under),
an optional token as one `{{secret:NAME}}` placeholder from the vault, a description for the model, a timeout and
whether it is enabled. A token is never stored in the setting itself.
"""

import re
from dataclasses import dataclass
from typing import Any

from app.secrets.service import PLACEHOLDER

NAME = re.compile(r"^[A-Za-z0-9_-]{1,30}$")
# The tool that calls these agents (app/a2a/tool.py); the permission decision refuses agents the model policy forbids.
A2A_TOOL = "call_agent"
MAX_AGENTS = 20
DEFAULT_TIMEOUT = 300
KEYS = {"url", "token", "description", "timeout", "enabled"}


@dataclass(frozen=True)
class AgentConfig:
    name: str
    url: str
    # A {{secret:NAME}} placeholder, or empty for an agent without a token.
    token: str = ""
    description: str = ""
    timeout: float = DEFAULT_TIMEOUT


def check_agent(name: str, value: Any) -> str | None:
    if not NAME.match(name):
        return f"Agent name {name!r}: use letters, digits, - and _, at most 30 characters."
    if not isinstance(value, dict):
        return f"Agent {name}: use an object."
    unknown = set(value) - KEYS
    if unknown:
        return f"Agent {name}: unknown keys {', '.join(sorted(unknown))}."
    url = value.get("url")
    if not (isinstance(url, str) and re.match(r"^https?://[^\s]+$", url)):
        return f"Agent {name}: url is an http(s) address."
    token = value.get("token", "")
    if not isinstance(token, str) or (token and not PLACEHOLDER.fullmatch(token.strip())):
        return f"Agent {name}: token is a secret placeholder such as {{{{secret:AGENT_TOKEN}}}}, never the token itself."
    description = value.get("description", "")
    if not isinstance(description, str) or len(description) > 500:
        return f"Agent {name}: description is text of at most 500 characters."
    if "timeout" in value and not (isinstance(value["timeout"], (int, float)) and 10 <= value["timeout"] <= 3600):
        return f"Agent {name}: timeout is a number of seconds from 10 to 3600."
    if "enabled" in value and not isinstance(value["enabled"], bool):
        return f"Agent {name}: enabled is true or false."
    return None


def validate_agents(value: Any, _providers: Any = None) -> str | None:
    if not isinstance(value, dict):
        return "Use an object with an agent per name."
    if len(value) > MAX_AGENTS:
        return f"At most {MAX_AGENTS} agents."
    for name, agent in value.items():
        problem = check_agent(str(name), agent)
        if problem:
            return problem
    return None


def parse_agents(value: Any) -> list[AgentConfig]:
    """The enabled agents of a stored setting value."""
    if not isinstance(value, dict) or validate_agents(value):
        return []
    return [
        AgentConfig(
            name=name,
            url=item["url"],
            token=str(item.get("token") or "").strip(),
            description=str(item.get("description") or ""),
            timeout=float(item.get("timeout", DEFAULT_TIMEOUT)),
        )
        for name, item in sorted(value.items())
        if item.get("enabled", True) is not False
    ]
