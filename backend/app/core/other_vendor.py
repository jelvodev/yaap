"""A model of another vendor: for work that judges what a vendor's model did.

The rubric scorer of the evals and the code review both want a judge that is not of the vendor whose model did the
work, so a vendor's model does not mark its own vendor's work. The choice starts from the task's own utility model
(46.8: the task's model, the preset's utility model, the utility_model setting); when that is of a vendor to avoid, or
cannot be used, it takes the cheapest catalog model of another vendor that the model policy, the connection, the key
and the budget allow. Only catalog models count: a local model's quality as a judge is unknown, and its price says
nothing about it. Everything goes through resolve_provider() and confirm_pick(), so nothing here decides a policy.
"""

from collections.abc import Collection, Iterable

from sqlalchemy.orm import Session

from app.core.model_choice import (
    ForUtility,
    ModelChoice,
    ModelChoiceError,
    confirm_pick,
    resolve_provider,
    utility_pick,
)
from app.providers.catalog import ModelInfo
from app.providers.registry import ProviderRegistry


class NoOtherVendor(ModelChoiceError):
    """No model of another vendor can be used here; the message says why, per vendor."""


def other_vendor_models(models: Iterable[ModelInfo], vendors: str | Collection[str]) -> list[str]:
    """Models of other vendors than `vendors` (a provider name or several), cheapest first by their standard input
    and output price."""
    avoided = {vendors} if isinstance(vendors, str) else set(vendors)
    others = [model for model in models if model.provider not in avoided]
    others.sort(key=lambda model: (model.pricing.standard.input + model.pricing.standard.output, model.id))
    return [model.id for model in others]


def cheapest_other_vendor(
    session: Session, providers: ProviderRegistry, environment_id: int, request: ForUtility, vendors: Collection[str]
) -> tuple[ModelChoice | None, list[str]]:
    """The cheapest catalog model of another vendor that can serve the request, and otherwise why not: the refusal
    of each other vendor's cheapest model, one line per vendor."""
    refusals: dict[str, str] = {}
    for model in other_vendor_models(providers.fixed_models, vendors):
        info = providers.find_model(model)
        vendor = info.provider if info is not None else model
        try:
            return confirm_pick(session, utility_pick(session, providers, environment_id, request, model, None)), []
        except ModelChoiceError as error:
            refusals.setdefault(vendor, f"{providers.provider_label(vendor)}: {error}")
    return None, list(refusals.values())


def other_vendor_choice(
    session: Session, providers: ProviderRegistry, environment_id: int, request: ForUtility, vendors: Collection[str]
) -> ModelChoice:
    """The request's own utility model when it is of none of `vendors`, else the cheapest allowed catalog model of
    another vendor; NoOtherVendor says why there is none. With no vendors to avoid, any vendor will do."""
    try:
        default = resolve_provider(session, providers, environment_id, request)
    except ModelChoiceError:
        default = None
    if default is not None and default.spec.name not in vendors:
        return default
    found, refusals = cheapest_other_vendor(session, providers, environment_id, request, vendors)
    if found is not None:
        return found
    avoided = " or ".join(sorted(providers.provider_label(vendor) for vendor in vendors))
    what = f"model of another vendor than {avoided}" if avoided else "catalog model"
    if not refusals:
        raise NoOtherVendor(f"The catalog has no {what}.")
    raise NoOtherVendor(f"No {what} can be used here. " + " ".join(refusals))
