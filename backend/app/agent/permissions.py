"""The permission decision for one tool call, as a pure function.

`decide` gets the tool, its arguments and the facts of the run as plain values and says whether the call runs, waits
for the user's approval or is refused, why, and what the approval card shows. The gate in `app.agent.tools` gathers
the facts, calls it and acts on the result; nothing here reads the run, the database or the disk.

The rules, in the order they apply:

1. A read-only conversation refuses every tool that is not read-only, except those in `READ_ONLY_ALSO`.
2. Secret placeholders are refused outside the tools that fill them in, and when they name an unknown secret.
   An A2A call to an agent the model policy does not allow is refused too.
3. The tool's permission (the policy's per-tool choice where the user may change it) says whether it asks.
4. An allowlisted command or website runs without asking, unless the run has read outside content.
5. After outside content every tool that acts asks (the injection guard).
6. Full access runs everything without asking, except `ALWAYS_ASKS`.
7. A call with a secret always asks.
8. A changing tool on a computer whose changes YAAP cannot roll back asks, unless the conversation has full access.
9. A run without the user (a scheduled task) refuses what would ask.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from app.a2a.config import A2A_TOOL
from app.agent.policy import CONFIGURABLE_TOOLS, AccessLevel, ToolPolicy
from app.agent.toolkit import Permission
from app.checkpoints.service import CHANGING_TOOLS
from app.core.model_policy import ModelPolicy, agent_refusal
from app.core.why import Why
from app.secrets.service import PLACEHOLDER_TOOLS, has_placeholder, placeholder_names

# A read-only conversation may still call these besides the reading tools: web_fetch (which asks as usual), delegate
# (its sub-runs are read only too) and finish_goal.
READ_ONLY_ALSO = frozenset({"web_fetch", "delegate", "finish_goal"})
# What asks also with full access.
ALWAYS_ASKS = frozenset({"git_push"})

Verdict = Literal["allow", "ask", "refuse"]
Reason = Literal[
    # allow
    "auto",
    "allowlisted",
    "full_access",
    "asks_itself",
    # ask
    "permission",
    "outside_content",
    "secret",
    "cannot_roll_back",
    # refuse
    "read_only",
    "secret_not_allowed",
    "unknown_secret",
    "unattended",
    "model_policy",
]
# Where the run works: YAAP's container, the project's sandbox (changes can be rolled back from checkpoints), or a
# computer over SSH (they cannot).
Place = Literal["container", "sandbox", "computer"]


@dataclass(frozen=True)
class ToolFacts:
    """What the decision needs to know about the tool and this call of it."""

    name: str
    permission: Permission
    # The one-line description of the call (the tool's describe, else its name), the start of the approval card.
    description: str
    read_only: bool = False
    # The handler shows its own approval card, so the gate never asks for it.
    asks_itself: bool = False
    # Secret placeholders may be in any of its text arguments.
    takes_secrets: bool = False
    # For a conditional tool: whether this call needs approval (the tool's needs_approval).
    conditional_asks: bool = False


@dataclass(frozen=True)
class RunFacts:
    """What the decision needs to know about the run."""

    # Per-tool choices, allowlists and the conversation's access level.
    policy: ToolPolicy = field(default_factory=ToolPolicy)
    # A scheduled run: nobody is there to approve.
    unattended: bool = False
    place: Place = "container"
    # The computer's name, shown on the approval card when changes there cannot be rolled back.
    place_label: str = ""
    # Labels of the outside content the run has read, in the order it read them.
    outside_sources: tuple[str, ...] = ()
    # Names of the secrets the run may use.
    known_secrets: frozenset[str] = frozenset()
    # The model policy of the environment and the project: which A2A agents call_agent may reach.
    model_policy: ModelPolicy = field(default_factory=ModelPolicy)

    @property
    def access(self) -> AccessLevel:
        return self.policy.access

    @property
    def can_roll_back(self) -> bool:
        return self.place != "computer"


@dataclass(frozen=True)
class PermissionDecision:
    verdict: Verdict
    reason: Reason
    # For a refusal: the text the model gets instead of the tool's output.
    message: str = ""
    # For a call that asks: the summary line of the approval card.
    summary: str = ""
    # Why the injection guard applies to this call (it asks, and the optional check runs), or None.
    outside_content: str | None = None
    # The secrets the call uses, for the vault's log and the card.
    secret_names: tuple[str, ...] = ()
    # The rule that decided this call.
    why: Why | None = None


def permission_why(reason: Reason, tool: str, facts: RunFacts, permission: Permission | None = None) -> Why:
    """The rule behind one decision reason, in plain words, with the setting that drove it when there is one."""
    if reason == "read_only":
        return Why(
            "This conversation is read only, so tools that change things are refused.",
            "permission",
            "access",
            "conversation",
        )
    if reason == "permission":
        setting = "tool_permissions" if tool in CONFIGURABLE_TOOLS else None
        return Why(f"{tool} asks before it runs: its permission is '{permission or 'ask'}'.", "permission", setting)
    if reason == "allowlisted":
        setting = {"web_fetch": "web_allowlist"}.get(tool, "command_allowlist")
        return Why(f"{tool} matches the allowlist, so it runs without asking.", "permission", setting)
    if reason == "outside_content":
        return Why(
            "After the run read outside content, every tool that acts asks first (the injection guard).",
            "permission",
            "injection_check",
        )
    if reason == "full_access":
        rule = "This conversation has full access, so tools run without asking."
        return Why(rule, "permission", "access", "conversation")
    if reason == "secret":
        return Why("A call that uses a secret always asks first.", "permission")
    if reason == "cannot_roll_back":
        return Why(
            f"{tool} changes things on a computer where changes cannot be rolled back, so it asks first.", "permission"
        )
    if reason == "unattended":
        return Why(
            f"This is a run without the user, and {tool} needs approval, so it is refused instead of asked.",
            "permission",
        )
    if reason == "model_policy":
        return Why("The model policy does not allow this agent.", "model_policy", "model_policy")
    if reason == "secret_not_allowed":
        return Why(f"{tool} may not take secret placeholders; only the tools that fill them in may.", "permission")
    if reason == "unknown_secret":
        return Why("The call names a secret that is not available to this run.", "permission")
    if reason == "asks_itself":
        return Why(f"{tool} shows its own approval card, so the gate does not ask again.", "permission")
    setting = "tool_permissions" if tool in CONFIGURABLE_TOOLS else None
    return Why(f"{tool} is allowed to run without asking.", "permission", setting)


def outside_content_reason(labels: Sequence[str]) -> str | None:
    """The approval card's line for a run that read outside content, or None when it read none."""
    if not labels:
        return None
    shown = ", ".join(labels[:4]) + (f" and {len(labels) - 4} more" if len(labels) > 4 else "")
    return f"Asked because this run read outside content: {shown}."


def refuse(reason: Reason, message: str, why: Why | None = None) -> PermissionDecision:
    return PermissionDecision("refuse", reason, message=message, why=why)


def secrets_in_call(
    tool: ToolFacts, arguments: dict[str, Any], facts: RunFacts
) -> tuple[str, ...] | PermissionDecision:
    """The secrets this call names, or the refusal when it may not name any or names an unknown one."""
    if not has_placeholder(arguments):
        return ()
    if tool.name not in PLACEHOLDER_TOOLS and not tool.takes_secrets:
        return refuse(
            "secret_not_allowed",
            "Secret placeholders work only in the command of run_command and start_process, and in what the "
            "browser types. Never write a secret into a file, a URL or a message; ask the user to do that.",
            permission_why("secret_not_allowed", tool.name, facts),
        )
    source = str(arguments.get("command") or "") if tool.name in PLACEHOLDER_TOOLS else json.dumps(arguments)
    names = tuple(placeholder_names(source))
    unknown = [name for name in names if name not in facts.known_secrets]
    if unknown:
        return refuse(
            "unknown_secret",
            f"Unknown secret {', '.join(unknown)}. Available: {', '.join(sorted(facts.known_secrets)) or 'none'}.",
            permission_why("unknown_secret", tool.name, facts),
        )
    return names


def decide(tool: ToolFacts, arguments: dict[str, Any], facts: RunFacts) -> PermissionDecision:
    """Whether this call runs, asks or is refused, following the rules in the module's description."""
    name = tool.name
    if facts.access == "read_only" and not tool.read_only and name not in READ_ONLY_ALSO:
        return refuse(
            "read_only",
            f"Refused: this conversation is read only, so {name} cannot run. Say what you would do instead; the user "
            "can give the conversation more access in the chat.",
            permission_why("read_only", name, facts),
        )
    secrets = secrets_in_call(tool, arguments, facts)
    if isinstance(secrets, PermissionDecision):
        return secrets
    if name == A2A_TOOL and (refusal := agent_refusal(facts.model_policy, str(arguments.get("agent") or ""))):
        return refuse("model_policy", refusal, permission_why("model_policy", name, facts))
    permission = facts.policy.permission_for(name, tool.permission)
    ask = permission == "ask" or (permission == "conditional" and tool.conditional_asks)
    reason: Reason = "permission" if ask else "auto"
    guard = None if tool.read_only or tool.asks_itself else outside_content_reason(facts.outside_sources)
    if ask and guard is None and facts.policy.allows(name, arguments):
        ask, reason = False, "allowlisted"
    if guard is not None:
        ask, reason = True, "outside_content"
    if facts.access == "full" and name not in ALWAYS_ASKS and ask:
        ask, reason = False, "full_access"
    if secrets:
        ask, reason = True, "secret"
    irreversible = not facts.can_roll_back and name in CHANGING_TOOLS
    if irreversible and facts.access != "full" and reason != "secret":
        ask, reason = True, "cannot_roll_back"
    if not ask:
        why = permission_why(reason, name, facts, permission)
        return PermissionDecision("allow", reason, outside_content=guard, secret_names=secrets, why=why)
    if tool.asks_itself:
        why = permission_why("asks_itself", name, facts, permission)
        return PermissionDecision("allow", "asks_itself", outside_content=guard, secret_names=secrets, why=why)
    if facts.unattended:
        note = f" ({guard})" if guard else ""
        return refuse(
            "unattended",
            f"Refused: this is a scheduled run without the user, and {name} needs approval{note}. It was not run. "
            "Go on without it if you can, and say in your summary what needs the user.",
            permission_why("unattended", name, facts, permission),
        )
    summary = tool.description
    if secrets:
        summary += f" (uses secret {', '.join(secrets)})"
    if irreversible:
        summary += f" — on {facts.place_label}, which YAAP cannot roll back"
    why = permission_why(reason, name, facts, permission)
    return PermissionDecision("ask", reason, summary=summary, outside_content=guard, secret_names=secrets, why=why)
