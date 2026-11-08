"""Who does delegated work: a preset's worker model, or a coding agent CLI.

`delegate` takes `worker`: a preset by name, `claude_code` or `codex`. A CLI worker is the unmodified Claude Code or
Codex CLI, started the way the connections of the same name start it: its own tools off, only YAAP's tools
over MCP with the sub-run's token, signed in with the user's subscription. Which model and effort it uses is a setting
per CLI (`claude_code_worker`, `codex_worker`), global with an environment override.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from app.providers.registry import ProviderRegistry

if TYPE_CHECKING:  # The model choice imports CLI_WORKERS from this module at run time.
    from app.core.model_choice import ModelChoiceError, ModelPick


@dataclass(frozen=True)
class CliWorker:
    # The name `delegate` takes.
    name: str
    label: str
    # The provider whose models it runs and the connection that starts the CLI.
    provider: str
    connection: str
    # The setting with its model and effort.
    setting: str
    default_model: str
    default_effort: str


CLI_WORKERS: dict[str, CliWorker] = {
    worker.name: worker
    for worker in (
        CliWorker("claude_code", "Claude Code", "anthropic", "claude_code", "claude_code_worker", "claude-sonnet-5-5", "medium"),
        CliWorker("codex", "Codex", "openai", "codex", "codex_worker", "gpt-6.1-sol", "medium"),
    )
}


def default_value(worker: CliWorker) -> dict[str, str]:
    return {"model": worker.default_model, "effort": worker.default_effort}


def cli_worker_validator(provider: str) -> Any:
    """Check a CLI worker setting: an object with a model of the CLI's vendor and one of that model's efforts."""

    def validate(value: Any, providers: ProviderRegistry) -> str | None:
        if not isinstance(value, dict) or set(value) != {"model", "effort"}:
            return "Use an object with a model and an effort."
        model = providers.find_model(str(value["model"]))
        if model is None or model.provider != provider:
            return f"Choose a model of {providers.provider_label(provider)}."
        if value["effort"] not in model.efforts:
            return f"Effort must be one of: {', '.join(model.efforts)}."
        return None

    return validate


@dataclass(frozen=True)
class WorkerModel:
    model: str
    effort: str


@dataclass(frozen=True)
class WorkerChoice:
    """One worker `delegate` can name, as Settings › Models › Utility and workers lists it."""

    name: str
    kind: str  # preset or cli
    label: str
    model: str
    model_name: str
    effort: str
    connection: str
    billing: str  # metered or subscription
    available: bool
    unavailable_reason: str | None = None
    is_default: bool = False
    # False when the model policy forbids it; unavailable_reason then names the policy and its level.
    allowed: bool = True


def list_workers(session: Session, providers: ProviderRegistry, environment_id: int) -> list[WorkerChoice]:
    """Every preset's worker (what `worker: <preset>` runs on), then the coding agent CLIs. Whether each can run is
    the model choice's answer for that worker, without the budget, which says nothing about the worker itself."""
    # Imported here: the model choice imports CLI_WORKERS from this module.
    from app.core.model_choice import ForSubRun, ModelChoiceError, PolicyForbids, confirm_pick, pick_model
    from app.presets.service import list_presets

    def reason_for(request: ForSubRun) -> "tuple[ModelPick, str | None, bool]":
        """The pick, why it cannot run, and whether the model policy allows it."""
        pick = pick_model(session, providers, environment_id, request)
        try:
            confirm_pick(session, pick, check_budget=False)
        except PolicyForbids as error:
            return pick, str(error), False
        except ModelChoiceError as error:
            return pick, listed_reason(pick.cli, pick.model, pick.spec.display_name if pick.spec else "", error), True
        return pick, None, True

    choices = []
    for preset in list_presets(session, environment_id):
        pick, reason, allowed = reason_for(
            ForSubRun(preset=None, model=preset.model, effort=preset.effort, worker=preset.name)
        )
        choices.append(
            WorkerChoice(
                name=preset.name,
                kind="preset",
                label=preset.name,
                model=pick.model,
                model_name=pick.info.display_name if pick.info else pick.model,
                effort=pick.effort,
                connection=pick.spec.display_name if pick.spec else "",
                billing="subscription" if pick.spec is not None and not pick.spec.metered else "metered",
                available=reason is None,
                unavailable_reason=reason,
                is_default=preset.is_default,
                allowed=allowed,
            )
        )
    for worker in CLI_WORKERS.values():
        pick, reason, allowed = reason_for(ForSubRun(preset=None, model="", effort="", worker=worker.name))
        choices.append(
            WorkerChoice(
                name=worker.name,
                kind="cli",
                label=worker.label,
                model=pick.model,
                model_name=pick.info.display_name if pick.info else pick.model,
                effort=pick.effort,
                connection=worker.label,
                billing="subscription",
                available=reason is None,
                unavailable_reason=reason,
                allowed=allowed,
            )
        )
    return choices


def listed_reason(cli: CliWorker | None, model: str, connection: str, error: "ModelChoiceError") -> str:
    """Why a worker cannot run, in the words of the Workers panel rather than of a refused delegate call."""
    from app.core.model_choice import MissingKey, UnknownModel, WrongVendor  # The model choice imports this module.

    if cli is not None:
        if isinstance(error, (WrongVendor, UnknownModel)):
            return f"{cli.label} cannot run the model {model}."
        return f"{cli.label} is not available in this installation."
    if isinstance(error, UnknownModel):
        return str(error)
    if isinstance(error, MissingKey):
        return f"Add an {connection} API key in Settings to use this worker."
    return "This connection cannot run agents."


def cli_worker_model(session: Session, environment_id: int | None, worker: CliWorker) -> WorkerModel:
    """The model and effort a CLI worker runs with in this environment."""
    from app.core.settings import SETTING_DEFINITIONS, resolve_setting  # The settings import this module's validator.

    value = resolve_setting(session, SETTING_DEFINITIONS[worker.setting], environment_id).value
    if not isinstance(value, dict):
        value = default_value(worker)
    return WorkerModel(str(value.get("model") or worker.default_model), str(value.get("effort") or worker.default_effort))
