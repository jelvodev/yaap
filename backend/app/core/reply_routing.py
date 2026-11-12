"""Routing a reply when its preset routes: once per reply, before the reply starts.

The utility model (task `route`) names the kind of the message; core.routing decides which of the preset's models
answers. The choice holds for the whole reply, its model calls in a run included, because a prompt cache belongs to
one model; smaller models within a run still come in through `delegate`. A routed model must be of the
conversation's provider, so its connection, key and stored turns stay the same, and resolve_provider() must accept
it: a model the policy forbids is never picked, whatever the utility model answers.
"""

from dataclasses import replace

import anyio
from sqlalchemy.orm import Session

from app.core.model_choice import ForReply, ModelChoiceError, resolve_provider
from app.core.reply_services import ReplyServices
from app.core.routing import Route, RouteModel, RouteRole, decide_route, preset_routes, route_prompt
from app.core.utility import SWITCHED_OFF_REASON, UtilityContext, UtilitySkipped, run_utility_or_skip
from app.presets.service import find_preset_by_name
from app.storage.models import Conversation, Message, ModelPreset

# A reply waits at most this long for the utility model's answer before the main model answers.
ROUTE_TIMEOUT_SECONDS = 20
ROUTE_MAX_TOKENS = 20


def routing_preset(session: Session, conversation: Conversation) -> ModelPreset | None:
    """The conversation's preset when it routes."""
    if not conversation.preset:
        return None
    preset = find_preset_by_name(session, conversation.environment_id, conversation.preset)
    return preset if preset is not None and preset.routing else None


def routed_text(message: Message) -> str:
    """What the user asks in a message: its text, else the goal turn or command it carries."""
    blocks = message.content
    text = "\n\n".join(str(block.get("text") or "") for block in blocks if block.get("type") == "text").strip()
    if text:
        return text
    return "\n\n".join(
        str(block.get("text") or "") for block in blocks if block.get("type") in ("goal", "command")
    ).strip()


def route_reply(services: ReplyServices, session: Session, conversation: Conversation, message: Message) -> Route | None:
    """How the conversation's preset routes this reply; None when it does not route (no utility call then). Runs in
    a worker thread, as the reply service does."""
    preset = routing_preset(session, conversation)
    if preset is None:
        return None
    text, environment_id, conversation_id = routed_text(message), conversation.environment_id, conversation.id
    answer = anyio.from_thread.run(ask_kind, services.utility, environment_id, conversation_id, text)
    providers = services.providers

    def name(model: str) -> str:
        info = providers.find_model(model)
        return info.display_name if info is not None else model

    def refusal(candidate: RouteModel) -> str | None:
        info = providers.find_model(candidate.model)
        if info is None:
            return "the model is not known."
        if info.provider != conversation.provider:
            return "it is of another provider than this conversation."
        request = ForReply(
            conversation.provider, candidate.model, candidate.effort, conversation.preset, conversation.project_id
        )
        try:
            resolve_provider(session, providers, conversation.environment_id, request)
        except ModelChoiceError as error:
            return str(error)
        return None

    main = RouteModel(conversation.model, conversation.effort)
    route = decide_route(
        None if isinstance(answer, UtilitySkipped) else answer,
        preset_routes(preset.routes),
        main,
        preset_roles(preset),
        refusal,
        name,
    )
    if isinstance(answer, UtilitySkipped) and answer.reason == SWITCHED_OFF_REASON:
        return replace(route, reason="Reply routing is switched off in Settings. The main model answers.")
    return route


def preset_roles(preset: ModelPreset) -> dict[RouteRole, RouteModel | None]:
    """The preset's worker and utility models; None for a role it leaves empty."""
    worker, utility = preset.worker_model, preset.utility_model
    return {
        "worker": RouteModel(worker, preset.worker_effort or preset.effort) if worker else None,
        "utility": RouteModel(utility, preset.utility_effort or preset.effort) if utility else None,
    }


async def ask_kind(
    context: UtilityContext, environment_id: int, conversation_id: int, text: str
) -> str | UtilitySkipped | None:
    """The utility model's answer to which kind the message is; None when it gives none in time."""
    with anyio.move_on_after(ROUTE_TIMEOUT_SECONDS):
        return await run_utility_or_skip(
            context, environment_id, route_prompt(text), conversation_id, max_tokens=ROUTE_MAX_TOKENS, task="route"
        )
    return None
