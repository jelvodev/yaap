"""Preparing a reply: what follows from the conversation, its environment and its project before anything is stored.

Preparing comes first, so a reply that cannot start (busy, no key, an unreachable computer) leaves the conversation
as it was.
"""

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from app.agents.definitions import AgentDefinition, agent_part, find_agent
from app.blueprint.context import build_system_parts, project_part
from app.blueprint.layers import Layer
from app.blueprint.levels import layers_for
from app.core.audit import NO_PROJECT_DATA, ProjectData, record_request, reply_entry, with_refusal
from app.core.model_choice import ForReply, ModelChoiceError, PolicyForbids, read_key, resolve_provider
from app.core.outgoing_checks import OutgoingRules, outgoing_rules
from app.core.prompts import Languages
from app.core.reply_notes import memory_note
from app.core.reply_errors import ReplyUnavailable
from app.core.reply_services import ReplyServices
from app.core.routing import Route
from app.core.search import recall_project_memory
from app.core.settings import resolve_languages
from app.core.turn_context import memory_parts, turn_context
from app.documents.service import document_part
from app.knowledge.service import project_documents
from app.projects.memory import has_entries, read_memory
from app.projects.service import project_path
from app.sandbox.host_path import project_host_folder
from app.providers.base import AgentRequest, SystemPart
from app.providers.registry import ProviderSpec
from app.secrets.service import list_secrets
from app.storage.models import Conversation, Environment, Project
from app.targets.base import TargetError
from app.targets.service import RunTarget, conversation_run_target, describe_target
from app.tracing.notes import Note


@dataclass
class ReplySetup:
    """Everything a reply needs that follows from the conversation, its environment and its project."""

    provider_spec: ProviderSpec
    api_key: str | None
    environment: Environment
    layers: list[Layer]
    agent_project: Project | None
    agent: AgentDefinition | None
    system: list[SystemPart]
    # The project part of the context, for delegated sub-runs.
    project_text: str | None = None
    # Date, time and recalled memory, added to the newest user message of the request.
    turn_context: str = ""
    languages: Languages = Languages()
    # The computer the run works on over SSH; None is YAAP's container.
    target: RunTarget | None = None
    # What the reply's model requests are checked for before they leave YAAP (secret values, listed patterns).
    outgoing: OutgoingRules = field(default_factory=OutgoingRules)
    # What of the project goes along besides the conversation, for the audit log.
    project_data: ProjectData = NO_PROJECT_DATA
    # The folder the run works in instead of the project folder: an eval case's throwaway workspace, which takes no
    # checkpoints in the project's shadow repository.
    workspace: Path | None = None
    # How the conversation's preset routed this reply; None when it does not route.
    route: Route | None = None
    # Step notes made while preparing: the memory recalled.
    notes: tuple[Note, ...] = ()

    def reply_model(self, conversation: Conversation) -> tuple[str, str]:
        """The model and effort of this reply: the routed ones, else the conversation's."""
        if self.route is not None:
            return self.route.model, self.route.effort
        return conversation.model, conversation.effort


def prepare_reply(services: ReplyServices, session: Session, conversation: Conversation, query: str = "") -> ReplySetup:
    """What a reply needs. query is the user's message, used to choose what of a large project memory is recalled."""
    model_request = ForReply(
        conversation.provider, conversation.model, conversation.effort, conversation.preset, conversation.project_id
    )
    try:
        choice = resolve_provider(session, services.providers, conversation.environment_id, model_request)
        api_key = read_key(session, services.cipher, services.credential_types, choice, conversation.id)
    except PolicyForbids as refusal:
        record_policy_refusal(services, conversation, refusal)
        raise ReplyUnavailable(str(refusal)) from refusal
    except ModelChoiceError as error:
        raise ReplyUnavailable(str(error)) from error
    provider_spec = choice.spec

    environment = session.get_one(Environment, conversation.environment_id)
    project = session.get(Project, conversation.project_id) if conversation.project_id else None
    layers = layers_for(services.blueprint_dir, services.workspace_dir, environment, project)
    # A conversation in a project runs as an agent with YAAP's tools, when the connection can run agents.
    agent_project = project if project is not None and provider_spec.runs_agents else None
    agent = find_agent(layers, conversation.agent)
    languages = resolve_languages(session, environment.id, project.id if project else None)
    project_text, recalled_memory, project_data = None, None, NO_PROJECT_DATA
    notes: tuple[Note, ...] = ()
    if agent_project is not None:
        project_text, recalled_memory, project_data, notes = project_context(
            services, session, agent_project, languages, query
        )
    system = build_system_parts(layers, project_text, agent_part(agent, layers) if agent else None, languages)
    on_target = None
    if provider_spec.runs_agents:
        try:
            on_target = conversation_run_target(session, services.cipher, conversation)
        except TargetError as error:
            raise ReplyUnavailable(str(error)) from error
    if on_target is not None:
        system.append(SystemPart(text=describe_target(on_target.target, on_target.root), source="target"))
    shared_document = document_part(session, conversation.id) if agent_project is not None else None
    if shared_document is not None:
        system.append(SystemPart(text=shared_document, source="document"))
    outgoing = outgoing_rules(session, services.cipher, environment.id, conversation.project_id)
    return ReplySetup(
        provider_spec, api_key, environment, layers, agent_project, agent, system, project_text,
        turn_context(session, recalled_memory), languages, on_target, outgoing, project_data, notes=notes,
    )


def record_policy_refusal(services: ReplyServices, conversation: Conversation, refusal: PolicyForbids) -> None:
    """A reply the model policy refused is recorded in the audit log, as a refused sub-run or utility task is. It
    would have carried the conversation; what of the project it would have carried is not known yet."""
    refused = AgentRequest(refusal.choice.model, refusal.choice.effort, [], [])
    entry = reply_entry(conversation, refusal.choice.spec, refused, NO_PROJECT_DATA, False)
    record_request(services.session_factory, with_refusal(entry, str(refusal)))


def project_context(
    services: ReplyServices, session: Session, project: Project, languages: Languages, query: str
) -> tuple[str, str | None, ProjectData, tuple[Note, ...]]:
    """The project part of the system prompt, the memory recalled for this message when it is too large, what of the
    project they carry, and the step note about the memory."""
    secret_names = [secret.name for secret in list_secrets(session, project.environment_id, project.id)]
    knowledge_names = [document.name for document in project_documents(session, project.id)]
    memory = read_memory(project_path(services.workspace_dir, project))
    recalled = recall_project_memory(session, services.indexer, project.environment_id, project.id, memory, query)
    memory_text, recalled_memory = memory_parts(recalled)
    # The memory counts whole or recalled; what is not recalled the model can still search.
    carried = ProjectData(has_entries(memory), bool(knowledge_names), tuple(sorted(secret_names)))
    host_folder = project_host_folder(services.host_data_dir, project.folder) if services.host_data_dir else None
    text = project_part(project, memory_text, secret_names, knowledge_names, languages.work, host_folder)
    found = memory_note(recalled)
    return text, recalled_memory, carried, (found,) if found is not None else ()
