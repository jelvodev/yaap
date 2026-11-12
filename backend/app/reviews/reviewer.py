"""Who gives a second opinion, and what it costs.

The reviewer is a model of another vendor than the one that wrote the reply (other_vendor_choice()). When no other
vendor is allowed, the strongest other model of the writer's own vendor that the policy, the connection, the key and
the budget allow is offered instead, marked as the same vendor, with the reason why no other vendor could be used;
using it is the person's choice. The ordering and the estimate are pure.
"""

from collections.abc import Collection, Iterable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.model_choice import ForUtility, ModelChoice, ModelChoiceError, confirm_pick, utility_pick
from app.core.other_vendor import NoOtherVendor, other_vendor_choice
from app.providers.catalog import ModelInfo, ModelPricing
from app.providers.registry import ProviderRegistry

# A token is about this many characters of text.
CHARS_PER_TOKEN = 4
# The effort of the same-vendor reviewer, when its model has it: the strongest model deserves some thinking.
SAME_VENDOR_EFFORT = "medium"


@dataclass(frozen=True)
class ReviewerPlan:
    """The model that would give the second opinion; `choice` is None when none can be used, and `reason` says why
    no other vendor was used (or why nothing was)."""

    choice: ModelChoice | None
    same_vendor: bool
    reason: str | None


@dataclass(frozen=True)
class Estimate:
    input_tokens: int
    output_tokens: int
    # None on a subscription connection, which is not paid per token, and for a model without a known price.
    usd: float | None


def same_vendor_order(models: Iterable[ModelInfo], vendors: Collection[str], written_by: Collection[str]) -> list[str]:
    """Models of the writer's own vendors, the strongest first (the highest standard input and output price), without
    the models that wrote the reply."""
    own = [model for model in models if model.provider in vendors and model.id not in written_by]
    own.sort(key=lambda model: (-(model.pricing.standard.input + model.pricing.standard.output), model.id))
    return [model.id for model in own]


def second_opinion_reviewer(
    session: Session,
    providers: ProviderRegistry,
    environment_id: int,
    request: ForUtility,
    vendors: Collection[str],
    written_by: Collection[str],
) -> ReviewerPlan:
    """Another vendor's model; else the writer's vendor's strongest other model; else no reviewer, with the reasons."""
    try:
        return ReviewerPlan(other_vendor_choice(session, providers, environment_id, request, vendors), False, None)
    except NoOtherVendor as error:
        why = str(error)
    refusals: dict[str, str] = {}
    for model in same_vendor_order(providers.fixed_models, vendors, written_by):
        try:
            pick = utility_pick(session, providers, environment_id, request, model, SAME_VENDOR_EFFORT)
            return ReviewerPlan(confirm_pick(session, pick), True, why)
        except ModelChoiceError as error:
            # One line per vendor is enough: its other models meet the same connection, key and policy.
            info = providers.find_model(model)
            vendor = info.provider if info is not None else model
            refusals.setdefault(vendor, f"{providers.provider_label(vendor)}: {error}")
    if not refusals:
        return ReviewerPlan(None, False, f"{why} The writer's vendor has no other model to ask.")
    others = " ".join(refusals.values())
    return ReviewerPlan(None, False, f"{why} Nor can another model of the same vendor be used. {others}")


def estimate_cost(
    prompt_chars: int, requests: int, max_tokens: int, pricing: ModelPricing | None, metered: bool
) -> Estimate:
    """What the requests cost at most: the prompts at about four characters a token and the longest replies, at the
    reviewer model's standard price. No money amount without a price or on a subscription."""
    input_tokens = -(-prompt_chars // CHARS_PER_TOKEN)
    output_tokens = requests * max_tokens
    if pricing is None or not metered:
        return Estimate(input_tokens, output_tokens, None)
    price = pricing.standard
    total = input_tokens * price.input + output_tokens * price.output
    return Estimate(input_tokens, output_tokens, round(total / 1_000_000, 6))
