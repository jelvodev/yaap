"""Why a decision came out as it did: the rule that decided it, and the setting and layer behind that rule.

Decisions the user meets (an approval asked, a tool refused, a model chosen or refused, the context shortened, the
cache lifetime, a hint) return a `Why` beside their result, without changing what they decide. The chat shows it in a
small "why" popover; step notes on the trace keep it with what happened.
"""

from dataclasses import dataclass
from typing import Literal, get_args

# Which kind of decision the reason belongs to.
WhySource = Literal[
    "permission",
    "route",
    "model_policy",
    "cache",
    "trim",
    "summary",
    "memory",
    "budget",
    "outgoing",
    "guard",
    "hint",
    "delegation",
]
# Where the setting behind a rule was set.
LayerKind = Literal["global", "environment", "project", "conversation"]

WHY_SOURCES: tuple[WhySource, ...] = get_args(WhySource)
LAYER_KINDS: tuple[LayerKind, ...] = get_args(LayerKind)


@dataclass(frozen=True)
class Why:
    # The rule, in plain words, as one or two sentences.
    rule: str
    source: WhySource
    # The setting that decided it, by its key (for example `trim_at` or `access`), or None for a fixed rule.
    setting: str | None = None
    # Where that setting was set, when known.
    layer: LayerKind | None = None

    def stored(self) -> dict[str, str | None]:
        """The reason as JSON, for a step note or a stored route."""
        return {"rule": self.rule, "source": self.source, "setting": self.setting, "layer": self.layer}


def why_from(value: object) -> Why | None:
    """A stored reason read back; None when the value is not one (a row written before reasons were kept)."""
    if not isinstance(value, dict):
        return None
    rule, source, setting, layer = value.get("rule"), value.get("source"), value.get("setting"), value.get("layer")
    if not isinstance(rule, str) or not rule:
        return None
    found_source = next((known for known in WHY_SOURCES if known == source), None)
    if found_source is None:
        return None
    found_layer = next((known for known in LAYER_KINDS if known == layer), None)
    return Why(rule, found_source, setting if isinstance(setting, str) and setting else None, found_layer)
