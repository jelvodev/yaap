"""The model policy: which connections, models and A2A agents an environment or project may use.

The setting `model_policy` holds an object per level, the environment and its projects. Every key is optional; a
missing key leaves that part open at the environment and inherits it at a project:

- `connections`: the connections that may be used, as `provider:connection` (for example `anthropic:api_key`).
- `models`: the model ids that may be used, or `"local"` for only models on a local connection (Ollama).
- `business_data`: true requires a model with the catalog's mark for business data processing; a local model meets
  it, as no vendor processes the data.
- `agents`: the A2A agents `call_agent` may reach, by name.
- `patterns`: regular expressions the outgoing check blocks (core/outgoing_checks.py).

A project can only narrow its environment's policy: both levels apply to every request, so a request must pass the
environment's rules and then the project's, and a refusal names the level whose rule it broke. Saving a project's
policy that would widen the environment's is refused with the reason (`widening_problem`).

Everything here is a pure function of plain values; reading the levels from the settings is in
core/model_policy_store.py.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from app.providers.registry import ProviderRegistry, ProviderSpec

LOCAL_ONLY = "local"
POLICY_KEYS = ("connections", "models", "business_data", "agents", "patterns")
MAX_ITEMS = 100
MAX_PATTERNS = 50
MAX_PATTERN_CHARS = 300
AGENT_NAME = re.compile(r"^[A-Za-z0-9_-]{1,30}$")
WHERE_TO_CHANGE = "Settings › Models › Policy"

PolicyLevelKind = Literal["environment", "project"]


@dataclass(frozen=True)
class LevelPolicy:
    """The policy one level stores. None leaves that part open (environment) or inherits it (project)."""

    connections: frozenset[str] | None = None
    models: frozenset[str] | None = None
    local_only: bool = False
    business_data: bool | None = None
    agents: frozenset[str] | None = None
    patterns: tuple[str, ...] = ()


@dataclass(frozen=True)
class PolicySource:
    """The level that set a rule, as a refusal names it."""

    kind: PolicyLevelKind
    name: str

    @property
    def label(self) -> str:
        return f"the {self.kind} {self.name}"


@dataclass(frozen=True)
class ModelPolicy:
    """The policies that apply to a request: the environment's, then the project's. Empty: everything is allowed."""

    levels: tuple[tuple[PolicySource, LevelPolicy], ...] = ()

    @property
    def patterns(self) -> tuple["ListedPattern", ...]:
        return tuple(ListedPattern(pattern, source) for source, level in self.levels for pattern in level.patterns)


@dataclass(frozen=True)
class ListedPattern:
    """A pattern the outgoing check blocks, with the level that listed it."""

    pattern: str
    source: PolicySource


@dataclass(frozen=True)
class PolicyTarget:
    """What a model request would use, as far as the policy looks at it."""

    model: str
    model_name: str
    # `provider:connection`, and the connection's name as Settings shows it.
    connection: str
    connection_name: str
    local: bool = False
    business_data: bool = False


@dataclass(frozen=True)
class PolicyChoices:
    """What a policy can name in this installation: connections by key and whether they are local, models by id."""

    connections: dict[str, bool] = field(default_factory=dict)
    local_models: frozenset[str] = frozenset()
    models: frozenset[str] = frozenset()


def connection_key(spec: ProviderSpec) -> str:
    return f"{spec.name}:{spec.connection}"


def policy_choices(providers: ProviderRegistry) -> PolicyChoices:
    connections = {connection_key(spec): spec.local for spec in providers.specs}
    local_providers = {spec.name for spec in providers.specs if spec.local}
    return PolicyChoices(
        connections=connections,
        local_models=frozenset(model.id for model in providers.models if model.provider in local_providers),
        models=frozenset(model.id for model in providers.models),
    )


def check_name_list(value: Any, what: str, pattern: re.Pattern[str] | None = None) -> str | None:
    if not isinstance(value, list) or len(value) > MAX_ITEMS:
        return f"{what}: give a list of at most {MAX_ITEMS} names."
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > 120:
            return f"{what}: each entry is a name of at most 120 characters."
        if pattern is not None and not pattern.match(item):
            return f"{what}: {item!r} is not a valid name."
    return None


def check_pattern(pattern: Any) -> str | None:
    """Why a listed pattern cannot be used, or None."""
    if not isinstance(pattern, str) or not pattern.strip() or len(pattern) > MAX_PATTERN_CHARS:
        return f"Each pattern is a regular expression of at most {MAX_PATTERN_CHARS} characters."
    try:
        compiled = re.compile(pattern)
    except re.error as error:
        return f"The pattern `{pattern}` is not a valid regular expression: {error}."
    if compiled.search("") is not None:
        return f"The pattern `{pattern}` matches empty text, so it would block every request."
    return None


def check_policy(value: Any, providers: ProviderRegistry) -> str | None:
    """Validate a stored policy: known keys, known connections and models, valid names and patterns."""
    if not isinstance(value, dict):
        return "Use an object with connections, models, business_data, agents and patterns."
    unknown = set(value) - set(POLICY_KEYS)
    if unknown:
        return f"Unknown keys: {', '.join(sorted(unknown))}. Use {', '.join(POLICY_KEYS)}."
    choices = policy_choices(providers)
    if "connections" in value:
        problem = check_name_list(value["connections"], "Connections")
        if problem:
            return problem
        strange = [item for item in value["connections"] if item not in choices.connections]
        if strange:
            return f"Unknown connections: {', '.join(strange)}. Known: {', '.join(sorted(choices.connections))}."
    models = value.get("models")
    if "models" in value and models != LOCAL_ONLY:
        problem = check_name_list(models, f'Models (a list, or "{LOCAL_ONLY}")')
        if problem:
            return problem
        strange = [item for item in models if item not in choices.models]
        if strange:
            return f"Unknown models: {', '.join(strange)}."
    if "business_data" in value and not isinstance(value["business_data"], bool):
        return "business_data is true or false."
    if "agents" in value:
        problem = check_name_list(value["agents"], "Agents", AGENT_NAME)
        if problem:
            return problem
    if "patterns" in value:
        patterns = value["patterns"]
        if not isinstance(patterns, list) or len(patterns) > MAX_PATTERNS:
            return f"Give at most {MAX_PATTERNS} patterns."
        for pattern in patterns:
            problem = check_pattern(pattern)
            if problem:
                return problem
    return None


def parse_level(value: Any) -> LevelPolicy:
    """A stored value as a level's policy. Saving validated it; what does not fit is left out rather than guessed."""
    if not isinstance(value, dict):
        return LevelPolicy()

    def names(key: str) -> frozenset[str] | None:
        items = value.get(key)
        return frozenset(str(item) for item in items) if isinstance(items, list) else None

    models = value.get("models")
    patterns = value.get("patterns")
    business_data = value.get("business_data")
    return LevelPolicy(
        connections=names("connections"),
        models=None if models == LOCAL_ONLY else names("models"),
        local_only=models == LOCAL_ONLY,
        business_data=business_data if isinstance(business_data, bool) else None,
        agents=names("agents"),
        patterns=tuple(p for p in patterns if check_pattern(p) is None) if isinstance(patterns, list) else (),
    )


def shown(items: Iterable[str]) -> str:
    listed = sorted(items)
    return ", ".join(listed) if listed else "none"


def level_refusal(level: LevelPolicy, target: PolicyTarget) -> str | None:
    """Which rule of one level the target breaks, as the end of a sentence, or None."""
    if level.connections is not None and target.connection not in level.connections:
        return f"the connection {target.connection_name} ({target.connection}) is not allowed"
    if level.local_only and not target.local:
        return f"only local models are allowed, and {target.model_name} is not one"
    if level.models is not None and target.model not in level.models:
        return f"the model {target.model_name} is not allowed (allowed: {shown(level.models)})"
    if level.business_data and not (target.business_data or target.local):
        return f"models must be marked for business data processing, and {target.model_name} is not"
    return None


def model_refusal(policy: ModelPolicy, target: PolicyTarget) -> str | None:
    """Why the policy refuses a model request, naming the policy and the level that set the rule, or None."""
    for source, level in policy.levels:
        broken = level_refusal(level, target)
        if broken is not None:
            return f"Refused by the model policy of {source.label}: {broken}. Change it in {WHERE_TO_CHANGE}."
    return None


def agent_refusal(policy: ModelPolicy, agent: str) -> str | None:
    """Why the policy refuses an outgoing A2A call to this agent, or None."""
    for source, level in policy.levels:
        if level.agents is not None and agent not in level.agents:
            return (
                f"Refused by the model policy of {source.label}: the A2A agent {agent} is not allowed (allowed: "
                f"{shown(level.agents)}). It was not called; go on without it or tell the user."
            )
    return None


def widening_problem(
    environment: LevelPolicy, project: LevelPolicy, environment_name: str, choices: PolicyChoices
) -> str | None:
    """Why a project's policy would widen its environment's, or None when it only narrows it."""
    intro = f"A project can only narrow the policy of the environment {environment_name}"
    if environment.connections is not None and project.connections is not None:
        extra = project.connections - environment.connections
        if extra:
            return f"{intro}, which allows the connections {shown(environment.connections)}; not {shown(extra)}."
    if environment.models is not None and project.models is not None:
        extra = project.models - environment.models
        if extra:
            return f"{intro}, which allows the models {shown(environment.models)}; not {shown(extra)}."
    if environment.local_only and project.models is not None:
        extra = project.models - choices.local_models
        if extra:
            return f"{intro}, which allows only local models; {shown(extra)} are not local."
    if environment.business_data and project.business_data is False:
        return f"{intro}, which requires models marked for business data processing; the project cannot drop that."
    if environment.agents is not None and project.agents is not None:
        extra = project.agents - environment.agents
        if extra:
            return f"{intro}, which allows the A2A agents {shown(environment.agents)}; not {shown(extra)}."
    return None
