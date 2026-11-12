"""Routing a reply: which of a preset's models answers one message, by its kind.

A preset that routes maps each kind of message to one of its own roles (main, worker, utility). Per reply the
utility model names the kind; decide_route() turns its answer into the model that answers, with a short reason the
chat shows. Everything here is pure: the caller (core/reply_routing.py) asks the utility model and says which models
the policy, the connection and the key allow. Any answer that names no single kind, and any model that cannot answer,
falls back to the conversation's own model, so routing never stops a reply.
"""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal, get_args

from app.blueprint.builtin import shipped_instructions
from app.core.why import Why

RouteKind = Literal["question", "change", "planning", "review"]
RouteRole = Literal["main", "worker", "utility"]

# The kinds of message in the order Settings shows them, with their label there.
ROUTE_KINDS: dict[RouteKind, str] = {
    "question": "Questions",
    "change": "Mechanical changes",
    "planning": "Planning",
    "review": "Review",
}
ROUTE_ROLES: tuple[RouteRole, ...] = get_args(RouteRole)
# How a reason names the kind it found.
KIND_PHRASES: dict[RouteKind, str] = {
    "question": "A question",
    "change": "A mechanical change",
    "planning": "Planning",
    "review": "A review",
}
# The message text the utility model classifies; enough to see what is asked.
MAX_ROUTED_TEXT = 4000
MAX_REASON = 300


@dataclass(frozen=True)
class RouteModel:
    """A model and effort that could answer: the conversation's own, or one of the preset's roles."""

    model: str
    effort: str


@dataclass(frozen=True)
class Route:
    """Which model answers this reply and why, as stored with the reply."""

    model: str
    effort: str
    model_name: str
    # The kind the utility model named; None when its answer named none.
    kind: RouteKind | None
    # The role that answers; main also when routing fell back.
    role: RouteRole
    reason: str
    why: Why | None = None

    def stored(self) -> dict[str, str | None]:
        return {
            "model": self.model,
            "effort": self.effort,
            "model_name": self.model_name,
            "kind": self.kind,
            "role": self.role,
            "reason": self.reason,
            "why": self.why.stored() if self.why else None,
        }


def preset_routes(stored: object) -> dict[RouteKind, RouteRole]:
    """Every kind with its role: the stored one when it is valid, else main."""
    known = stored if isinstance(stored, dict) else {}
    return {kind: known[kind] if known.get(kind) in ROUTE_ROLES else "main" for kind in ROUTE_KINDS}


def check_routes(value: Mapping[str, str]) -> str | None:
    """What is wrong with a change of routes (kind to role), or None."""
    for kind, role in value.items():
        if kind not in ROUTE_KINDS:
            return f"Unknown kind of message: {kind}."
        if role not in ROUTE_ROLES:
            return f"A kind of message goes to one of the preset's roles: {', '.join(ROUTE_ROLES)}."
    return None


def route_prompt(text: str, instructions: str | None = None) -> str:
    """What the utility model is asked: the kind of the message, as one word. The instructions may be replaced; the
    answer format that classify() parses stays here."""
    lead = instructions if instructions is not None else shipped_instructions("router")
    shortened = text.strip()[:MAX_ROUTED_TEXT] or "(no text)"
    return (
        f"{lead} Reply with one word:\n"
        "question - it asks for an explanation or information and changes nothing\n"
        "change - a small, mechanical change with clear instructions (rename, format, a one-line fix)\n"
        "planning - it asks for a design, a plan, or a larger or open task of several steps\n"
        "review - it asks to review or check code, a diff, a result or a decision\n\n"
        f"Message:\n{shortened}"
    )


def classify(answer: str | None) -> RouteKind | None:
    """The kind the utility model's answer names, when it names exactly one."""
    if not answer:
        return None
    words = set(re.findall(r"[a-z]+", answer.lower()))
    named = [kind for kind in ROUTE_KINDS if kind in words]
    return named[0] if len(named) == 1 else None


def decide_route(
    answer: str | None,
    routes: Mapping[RouteKind, RouteRole],
    main: RouteModel,
    roles: Mapping[RouteRole, RouteModel | None],
    refusal: Callable[[RouteModel], str | None],
    name: Callable[[str], str],
) -> Route:
    """The model that answers a reply. answer is the utility model's (None when it gave none); routes maps each kind
    to a role; main is the conversation's own model; roles holds the preset's other models (None for an empty role);
    refusal says why a model cannot answer here (the policy, its connection, its key), or None; name gives a model's
    display name. Every way it cannot route ends on the main model with the reason."""

    def fallback(kind: RouteKind | None, reason: str, rule: str, setting: str | None = None) -> Route:
        ending = " The main model answers."
        reason = reason if len(reason) + len(ending) <= MAX_REASON else reason[: MAX_REASON - len(ending) - 1] + "…"
        why = Why(rule + ending, "route", setting)
        return Route(main.model, main.effort, name(main.model), kind, "main", reason + ending, why)

    kind = classify(answer)
    if kind is None:
        if answer:
            return fallback(
                None,
                "The utility model's answer named no kind of message.",
                "Routing needs the utility model to name one kind of message, and its answer named none.",
            )
        return fallback(None, "The utility model gave no answer.", "Routing needs an answer from the utility model.")
    phrase, role = KIND_PHRASES[kind], routes.get(kind, "main")
    if role == "main":
        rule = f"The preset sends {kind} messages to the main model."
        why = Why(rule, "route", "routes")
        return Route(main.model, main.effort, name(main.model), kind, "main", f"{phrase}: the main model answers.", why)
    candidate = roles.get(role)
    if candidate is None:
        return fallback(
            kind,
            f"{phrase} goes to the {role} model, which this preset does not have.",
            f"The preset sends {kind} messages to its {role} model, but the preset has none.",
            "routes",
        )
    problem = refusal(candidate)
    if problem is not None:
        return fallback(
            kind,
            f"{phrase} goes to {name(candidate.model)}, which cannot answer here: {problem}",
            f"The preset sends {kind} messages to its {role} model, but this connection, the model policy or the key "
            "does not allow it here.",
            "routes",
        )
    reason = f"{phrase}: the {role} model answers."
    why = Why(f"The preset sends {kind} messages to its {role} model.", "route", "routes")
    return Route(candidate.model, candidate.effort, name(candidate.model), kind, role, reason, why)
