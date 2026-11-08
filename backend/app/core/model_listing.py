"""Whether each model of the catalog can answer in an environment (and a project), as the model lists show it.

The answer is the model choice's own (pick_model and confirm_pick for a reply with that model), without the budget,
which says nothing about the model itself. So a listing and a reply agree on the connection, the key and the model
policy, and the policy's refusal text is the one a reply would get.
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.model_choice import (
    ConnectionUnavailable,
    ForReply,
    MissingKey,
    ModelChoiceError,
    PolicyForbids,
    confirm_pick,
    pick_model,
)
from app.providers.catalog import ModelInfo
from app.providers.registry import ProviderRegistry


@dataclass(frozen=True)
class ModelAvailability:
    """Whether a model can answer now; allowed is False when the model policy forbids it, and reason then names the
    policy and its level."""

    available: bool = True
    allowed: bool = True
    reason: str | None = None


def model_availability(
    session: Session, providers: ProviderRegistry, environment_id: int, model: ModelInfo, project_id: int | None
) -> ModelAvailability:
    request = ForReply(model.provider, model.id, model.default_effort, project_id=project_id)
    pick = pick_model(session, providers, environment_id, request)
    try:
        confirm_pick(session, pick, check_budget=False)
    except PolicyForbids as refusal:
        return ModelAvailability(available=False, allowed=False, reason=str(refusal))
    except ModelChoiceError as error:
        connection = pick.spec.display_name if pick.spec else ""
        return ModelAvailability(available=False, reason=listed_reason(error, connection))
    return ModelAvailability()


def listed_reason(error: ModelChoiceError, connection: str) -> str:
    """Why a model cannot answer, in the words of a model list rather than of a refused reply."""
    if isinstance(error, MissingKey):
        return f"Add an {connection} API key in Settings to use this model."
    if isinstance(error, ConnectionUnavailable):
        return "This connection is not available."
    return str(error)
