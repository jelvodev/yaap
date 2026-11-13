"""One way to choose provider, model and key: every model request passes through resolve_provider().

A request names who asks (a conversation's reply, a delegated sub-run, the utility model) and what it starts from
(the conversation's preset and model, a named preset or CLI worker, a role). resolve_provider() turns that into a
ModelChoice: the model and effort, the connection and its adapter, a reference to the key it needs and how it is
billed. It refuses a request that cannot be served with a ModelChoiceError whose message is written for the person
or the model that asked; callers in `api/` translate it to a response, the others to their own fallback.

The key's value is not part of the choice. read_key() reads it when the request is about to be made, so the vault's
log records the use, and a listing can check that a key exists without reading it.
"""

from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from app.agent.workers import CLI_WORKERS, CliWorker, cli_worker_model
from app.core.budget import budget_refusal
from app.core.model_policy import PolicyTarget, connection_key, model_refusal
from app.core.model_policy_store import resolve_model_policy
from app.core.settings import SETTING_DEFINITIONS, resolve_connection, resolve_setting, resolve_text
from app.core.utility_tasks import SETTING_KEY as UTILITY_TASKS_KEY
from app.core.utility_tasks import task_model
from app.credentials.crypto import CredentialCipher, CredentialDecryptionError
from app.credentials.store import find_credential, read_credential_value
from app.credentials.types import CredentialType
from app.presets.service import find_preset_by_name, role_model
from app.providers.catalog import ModelInfo, default_model
from app.providers.registry import ProviderRegistry, ProviderSpec

Purpose = Literal["reply", "sub_run", "utility"]
Billing = Literal["metered", "subscription"]
SubRunRole = Literal["worker", "main"]
UtilityRole = Literal["utility", "worker"]

# How a key's use is named in the vault's log, after the connection's name.
KEY_USE: dict[Purpose, str] = {"reply": "reply", "sub_run": "delegated task", "utility": "utility model"}


@dataclass(frozen=True)
class ForReply:
    """A reply in a conversation: the provider, model and effort the conversation stores."""

    provider: str
    model: str
    effort: str
    preset: str | None = None
    # The conversation's project, whose model policy applies besides the environment's.
    project_id: int | None = None


@dataclass(frozen=True)
class ForSubRun:
    """A delegated sub-run. Without worker, the conversation's preset by role, or the preset of a named
    agent; worker names a preset of the environment or a CLI worker (`claude_code`, `codex`)."""

    preset: str | None
    # The conversation's own model and effort, the last fallback when no preset has the role.
    model: str
    effort: str
    role: SubRunRole = "worker"
    worker: str | None = None
    agent_preset: str | None = None
    agent_role: str | None = None
    project_id: int | None = None


@dataclass(frozen=True)
class ForUtility:
    """A small internal task: the task's own model (utility_task_models), else the preset's utility model (its
    worker model with role worker), else the utility_model setting."""

    preset: str | None = None
    role: UtilityRole = "utility"
    project_id: int | None = None
    # The task by name (`title`, `commit_message`), for a model of its own.
    task: str | None = None


ModelRequest = ForReply | ForSubRun | ForUtility


@dataclass(frozen=True)
class KeyReference:
    """Which stored key a choice needs; read_key() reads its value."""

    environment_id: int
    kind: str


@dataclass(frozen=True)
class ModelPick:
    """Which model a request asks for and the connection that would reach it, before any check."""

    purpose: Purpose
    environment_id: int
    model: str
    effort: str
    # The catalog entry; None when the model is not in the catalog.
    info: ModelInfo | None
    # The connection's adapter; None when the installation has no such connection.
    spec: ProviderSpec | None
    # The preset the request started from, by name; None for a CLI worker or a conversation without one.
    preset: str | None = None
    # The CLI worker, when the request named one.
    cli: CliWorker | None = None
    # A named worker as stored on the run record (the preset's name, `claude_code` or `codex`) and as shown.
    worker: str | None = None
    worker_label: str | None = None
    # The project whose model policy applies besides the environment's.
    project_id: int | None = None


@dataclass(frozen=True)
class ModelChoice:
    """A request that can be served: what resolve_provider() returns."""

    purpose: Purpose
    environment_id: int
    model: str
    effort: str
    info: ModelInfo | None
    spec: ProviderSpec
    connection: str
    key: KeyReference | None
    billing: Billing
    preset: str | None = None
    cli: CliWorker | None = None
    worker: str | None = None
    worker_label: str | None = None


class ModelChoiceError(Exception):
    """A model request cannot be served; the message says why, for the person or the model that asked."""


class UnknownWorker(ModelChoiceError):
    """delegate named a worker that is neither a preset of the environment nor a CLI worker."""


class UnknownModel(ModelChoiceError):
    """The model is not in the catalog."""


class ConnectionUnavailable(ModelChoiceError):
    """The installation has no adapter for the provider and connection."""


class WrongVendor(ModelChoiceError):
    """A CLI worker's setting names a model of another vendor."""


class CannotRunAgents(ModelChoiceError):
    """The connection cannot run an agent with YAAP's tools, which a sub-run needs."""


class BudgetReached(ModelChoiceError):
    """The environment's budget refuses metered requests."""


class MissingKey(ModelChoiceError):
    """The connection needs a key the environment does not have."""


class UnusableKey(ModelChoiceError):
    """The stored key cannot be decrypted or has the wrong format."""


class PolicyForbids(ModelChoiceError):
    """The model policy of the environment or the project does not allow the model or its connection. choice is
    what the request would have gone to, so the refusal can be recorded in the audit log."""

    def __init__(self, message: str, choice: ModelChoice) -> None:
        super().__init__(message)
        self.choice = choice


def resolve_provider(
    session: Session,
    providers: ProviderRegistry,
    environment_id: int,
    request: ModelRequest,
    check_budget: bool = True,
) -> ModelChoice:
    """The provider, model and key for a model request, or a ModelChoiceError saying why it cannot be served.

    check_budget is False only for a listing that makes no request (who `delegate` can name). The utility model
    falls back to the utility_model setting when the policy refuses the preset's model; nothing else falls back."""
    pick = pick_model(session, providers, environment_id, request)
    try:
        return confirm_pick(session, pick, check_budget)
    except PolicyForbids as refusal:
        fallback = utility_fallback(session, providers, environment_id, request, pick)
        if fallback is None:
            raise
        try:
            return confirm_pick(session, fallback, check_budget)
        except PolicyForbids:
            raise refusal from None


def pick_model(session: Session, providers: ProviderRegistry, environment_id: int, request: ModelRequest) -> ModelPick:
    """Which model and connection the request asks for. Raises only UnknownWorker; confirm_pick() checks the rest."""
    if isinstance(request, ForReply):
        return pick_for_reply(session, providers, environment_id, request)
    if isinstance(request, ForSubRun):
        return pick_for_sub_run(session, providers, environment_id, request)
    return pick_for_utility(session, providers, environment_id, request)


def pick_for_reply(session: Session, providers: ProviderRegistry, environment_id: int, request: ForReply) -> ModelPick:
    spec = providers.find_provider(request.provider, resolve_connection(session, request.provider, environment_id))
    info = providers.find_model(request.model)
    return ModelPick(
        "reply", environment_id, request.model, request.effort, info, spec, preset=request.preset,
        project_id=request.project_id,
    )


def pick_for_sub_run(
    session: Session, providers: ProviderRegistry, environment_id: int, request: ForSubRun
) -> ModelPick:
    """No worker: the conversation's preset, chosen by role and agent. A preset by name: its worker model (its main
    model with role main). claude_code or codex: that CLI with the model of its setting."""
    preset_name = request.preset
    worker = worker_label = None
    cli = CLI_WORKERS.get(request.worker) if request.worker else None
    if not request.worker:
        model, effort, preset_name = sub_run_role_model(session, environment_id, request)
    elif cli is not None:
        chosen = cli_worker_model(session, environment_id, cli)
        model, effort, preset_name, worker, worker_label = chosen.model, chosen.effort, None, cli.name, cli.label
    else:
        preset = find_preset_by_name(session, environment_id, request.worker)
        if preset is None:
            raise UnknownWorker(
                f"No worker named '{request.worker}'. Name a preset of this environment, {' or '.join(CLI_WORKERS)}."
            )
        if request.role == "main" or not preset.worker_model:
            model, effort = preset.model, preset.effort
        else:
            model, effort = preset.worker_model, preset.worker_effort or preset.effort
        preset_name = worker = worker_label = preset.name
    info = providers.find_model(model)
    if info is not None and effort not in info.efforts:
        effort = info.default_effort
    if cli is not None:
        # A CLI worker always starts its CLI, whatever connection the environment uses for the vendor.
        spec = providers.find_provider(cli.provider, cli.connection)
    elif info is not None:
        spec = providers.find_provider(info.provider, resolve_connection(session, info.provider, environment_id))
    else:
        spec = None
    return ModelPick(
        "sub_run", environment_id, model, effort, info, spec, preset_name, cli, worker, worker_label, request.project_id
    )


def sub_run_role_model(session: Session, environment_id: int, request: ForSubRun) -> tuple[str, str, str | None]:
    """Model, effort and preset for a sub-run without a named worker."""
    if request.agent_preset and request.agent_role != "worker":
        named = find_preset_by_name(session, environment_id, request.agent_preset)
        if named is not None:
            return named.model, named.effort, named.name
    role = "main" if request.role == "main" and request.agent_role != "worker" else "worker"
    chosen = role_model(session, environment_id, request.preset, role)
    return (chosen.model, chosen.effort, request.preset) if chosen else (request.model, request.effort, request.preset)


def pick_for_utility(
    session: Session, providers: ProviderRegistry, environment_id: int, request: ForUtility
) -> ModelPick:
    own = utility_task_model(session, environment_id, request) if request.role == "utility" else None
    if own is not None:
        return utility_pick(session, providers, environment_id, request, own, None)
    chosen = (role_model(session, environment_id, request.preset, "worker") if request.role == "worker" else None) or (
        role_model(session, environment_id, request.preset, "utility")
    )
    model = chosen.model if chosen else utility_setting_model(session, providers, environment_id)
    return utility_pick(session, providers, environment_id, request, model, chosen.effort if chosen else None)


def utility_task_model(session: Session, environment_id: int, request: ForUtility) -> str | None:
    """The model utility_task_models names for the request's task; never one for the outside-content check."""
    if request.task is None:
        return None
    task_models = resolve_setting(session, SETTING_DEFINITIONS[UTILITY_TASKS_KEY], environment_id).value
    return task_model(request.task, task_models if isinstance(task_models, dict) else {})


def utility_setting_model(session: Session, providers: ProviderRegistry, environment_id: int) -> str:
    # An empty setting means the default vendor's utility model: the registry's first, else the built-in one.
    setting = resolve_text(session, "utility_model", environment_id)
    return setting or providers.default_model("utility") or default_model("utility")


def utility_pick(
    session: Session,
    providers: ProviderRegistry,
    environment_id: int,
    request: ForUtility,
    model: str,
    chosen_effort: str | None,
) -> ModelPick:
    """The utility model's pick: the chosen effort when the model has it, else low."""
    info = providers.find_model(model)
    preset, project_id = request.preset, request.project_id
    if info is None:
        effort = chosen_effort or "low"
        return ModelPick("utility", environment_id, model, effort, None, None, preset, project_id=project_id)
    if chosen_effort in info.efforts:
        effort = chosen_effort
    else:
        effort = "low" if "low" in info.efforts else info.default_effort
    spec = providers.find_provider(info.provider, resolve_connection(session, info.provider, environment_id))
    return ModelPick("utility", environment_id, model, effort, info, spec, preset, project_id=project_id)


def utility_fallback(
    session: Session, providers: ProviderRegistry, environment_id: int, request: ModelRequest, refused: ModelPick
) -> ModelPick | None:
    """For a utility request whose model the policy refused: the utility_model setting's model, when that is another
    model. Other requests have no fallback."""
    if not isinstance(request, ForUtility):
        return None
    model = utility_setting_model(session, providers, environment_id)
    if model == refused.model:
        return None
    return utility_pick(session, providers, environment_id, request, model, None)


def confirm_pick(session: Session, pick: ModelPick, check_budget: bool = True) -> ModelChoice:
    """The pick as a choice that can be served, or the first reason it cannot, in the order a request meets them:
    the model and connection, the model policy, the budget, the key."""
    spec = check_connection(pick)
    choice = ModelChoice(
        purpose=pick.purpose,
        environment_id=pick.environment_id,
        model=pick.model,
        effort=pick.effort,
        info=pick.info,
        spec=spec,
        connection=spec.connection,
        key=KeyReference(pick.environment_id, spec.credential_kind) if spec.credential_kind is not None else None,
        billing="metered" if spec.metered else "subscription",
        preset=pick.preset,
        cli=pick.cli,
        worker=pick.worker,
        worker_label=pick.worker_label,
    )
    policy = resolve_model_policy(session, pick.environment_id, pick.project_id)
    refusal = model_refusal(policy, policy_target(pick, spec))
    if refusal is not None:
        raise PolicyForbids(refusal, choice)
    if check_budget and spec.metered and (refusal := budget_refusal(session, pick.environment_id)):
        raise BudgetReached(refusal)
    if choice.key is not None and find_credential(session, pick.environment_id, choice.key.kind) is None:
        raise MissingKey(missing_key_message(pick, spec))
    return choice


def check_connection(pick: ModelPick) -> ProviderSpec:
    """The pick's adapter, when its model and connection can serve the request's purpose."""
    if pick.purpose == "reply":
        if pick.spec is None:
            raise ConnectionUnavailable("The provider of this conversation is not available.")
        return pick.spec
    if pick.cli is not None:
        if pick.spec is None:
            raise ConnectionUnavailable(f"{pick.cli.label} is not available as a worker in this installation.")
        if pick.info is None or pick.info.provider != pick.cli.provider:
            raise WrongVendor(
                f"{pick.cli.label} cannot run the model {pick.model}; choose another in "
                "Settings › Models › Utility and workers."
            )
    if pick.info is None:
        raise UnknownModel(f"The model {pick.model} is not known.")
    if pick.purpose == "sub_run" and (pick.spec is None or not pick.spec.runs_agents):
        raise CannotRunAgents(f"The connection for {pick.info.display_name} cannot run agents in this environment.")
    if pick.spec is None:
        raise ConnectionUnavailable(f"The connection for {pick.info.display_name} is not available.")
    return pick.spec


def policy_target(pick: ModelPick, spec: ProviderSpec) -> PolicyTarget:
    """What the model policy looks at in a pick whose connection is known."""
    return PolicyTarget(
        model=pick.model,
        model_name=pick.info.display_name if pick.info is not None else pick.model,
        connection=connection_key(spec),
        connection_name=spec.display_name,
        local=spec.local,
        business_data=pick.info.business_data if pick.info is not None else False,
    )


def missing_key_message(pick: ModelPick | ModelChoice, spec: ProviderSpec) -> str:
    if pick.purpose == "sub_run" and pick.info is not None:
        return f"No {spec.display_name} API key in this environment for {pick.info.display_name}."
    return f"No {spec.display_name} API key in this environment. Add one in Settings."


def read_key(
    session: Session,
    cipher: CredentialCipher,
    credential_types: dict[str, CredentialType],
    choice: ModelChoice,
    conversation_id: int | None = None,
) -> str | None:
    """The key's value for a request about to be made, its use recorded in the vault's log; None when the
    connection needs no key (Claude Code, Codex). A stored subscription token is refused like any wrong format."""
    if choice.key is None:
        return None
    try:
        value = read_credential_value(
            session,
            cipher,
            choice.key.environment_id,
            choice.key.kind,
            used_by=f"{choice.spec.display_name} {KEY_USE[choice.purpose]}",
            conversation_id=conversation_id,
        )
    except CredentialDecryptionError as error:
        raise UnusableKey(str(error)) from error
    if value is None:
        raise MissingKey(missing_key_message(choice, choice.spec))
    credential_type = credential_types.get(choice.key.kind)
    problem = credential_type.find_format_problem(value) if credential_type else None
    if problem is not None:
        raise UnusableKey(f"{problem} Replace the stored key in Settings.")
    return value
