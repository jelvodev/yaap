"""The agent run of a reply: its tools, policy, secrets and the services its tools use.

Every conversation is an agent run when the connection can run one. Outside a project it gets only the tools that
need no project folder (web search and fetch, skills, standards), and read-only tools for attachments.
"""

from dataclasses import replace
from pathlib import Path

import anyio
from sqlalchemy.orm import Session

from app.a2a.tool import TOOL_NAME as A2A_TOOL, agent_tools
from app.agent.code_tools import code_intel_for_run
from app.agent.delegation import Delegator
from app.agent.guard import utility_injection_check
from app.agent.outputs import OutputStore
from app.agent.runs import AgentRun, AttachmentRef, InjectionCheck, RunConfig, RunServices, SecretUseRecorder
from app.agent.tool_catalog import ToolCatalog
from app.agent.toolkit import Tool
from app.agent.tools import tools_for
from app.agents.records import start_reply_record, start_run_record
from app.attachments.service import attachment_folder
from app.browser.service import browser_tools
from app.checkpoints.service import CheckpointStore
from app.core.github_token import read_github_token
from app.core.goals import ACTIVE, GoalHandle, open_goal
from app.core.model_policy_store import resolve_model_policy
from app.core.reply_services import ReplyServices
from app.core.reply_setup import ReplySetup
from app.core.message_queue import QueueSteeringHandoff
from app.core.search import RunSearch
from app.core.settings import SETTING_DEFINITIONS, resolve_setting, resolve_text, resolve_tool_policy
from app.documents.service import DocumentLink
from app.mcp_client.runs import RunMcpTools, load_run_tools, server_place
from app.projects.service import project_path
from app.scheduling.active import active_run
from app.secrets.service import record_secret_uses, resolve_secrets
from app.storage.models import Attachment, Conversation
from app.targets.service import allowed_access
from app.tracing.provider import ModelTrace, catalog_pricing
from app.tracing.recorder import StepListener, TraceRecorder
from app.tracing.snapshots import SnapshotStore, trace_dir


def secret_use_recorder(
    services: ReplyServices, environment_id: int, project_id: int | None, conversation_id: int
) -> SecretUseRecorder:
    """A callback that notes in the vault's log which secrets a run filled in."""

    def record(names: list[str], used_by: str) -> None:
        with services.session_factory() as use_session:
            record_secret_uses(use_session, environment_id, project_id, names, used_by, conversation_id)

    return record


def start_agent_run(
    services: ReplyServices,
    session: Session,
    conversation: Conversation,
    setup: ReplySetup,
    attachments: list[Attachment],
    read_only: bool,
    unattended: bool,
    tools_by_name: tuple[str, ...] = (),
    on_step: StepListener | None = None,
) -> AgentRun:
    """Start the run of a reply. read_only makes this one run read only (/check); unattended means nobody approves,
    and tools_by_name then run without asking (an eval case's allowed_tools). on_step hears every recorded step."""
    agent = setup.agent
    # An agent with a tools list gets only those; without one, every tool the conversation offers.
    allowed_tools = set(agent.tools) if agent is not None and agent.tools is not None else None
    config = run_config(services, session, conversation, setup, attachments, allowed_tools)
    config = limit_access(session, config, conversation, setup, read_only, unattended)
    if config.unattended and tools_by_name:
        config = replace(config, policy=replace(config.policy, tools=(*config.policy.tools, *tools_by_name)))
    parts = run_services(services, session, conversation, setup, allowed_tools, on_step)
    run = services.runs.start(config, parts)
    try:
        # A new reply is activity in the conversation, so its background processes keep running.
        services.processes.touch_conversation(conversation.id)
        run.state.record_id = start_run_record(session, run, setup.reply_model(conversation)[0])
    except BaseException:  # A failed start must not leave a usable tool-server token behind.
        services.runs.finish(run)
        raise
    return run


def run_config(
    services: ReplyServices,
    session: Session,
    conversation: Conversation,
    setup: ReplySetup,
    attachments: list[Attachment],
    allowed_tools: set[str] | None,
) -> RunConfig:
    environment, project, target = setup.environment, setup.agent_project, setup.target
    project_id = project.id if project is not None else None
    author_name = resolve_text(session, "git_author_name", environment.id)
    author_email = resolve_text(session, "git_author_email", environment.id)
    return RunConfig(
        conversation_id=conversation.id,
        environment_id=environment.id,
        blueprint_layers=setup.layers,
        # A sandbox and the runner offer the container's tools: their project folder is the one in YAAP.
        tool_names=tools_for(
            project is not None, bool(attachments), allowed_tools, target is not None and not target.has_container_tools
        ),
        agent=setup.agent.name if setup.agent else None,
        project_id=project_id,
        project_folder=run_folder(services, setup),
        attachments=[
            AttachmentRef(
                id=attachment.id,
                name=attachment.filename,
                kind=attachment.kind,
                media_type=attachment.media_type,
                folder=attachment_folder(services.files_dir, attachment),
            )
            for attachment in attachments
        ],
        git_author=(author_name, author_email) if author_name and author_email else None,
        github_token=read_github_token(session, services.cipher, environment.id, project_id, conversation.id),
        policy=replace(
            resolve_tool_policy(session, environment.id, project_id), access=conversation.access or "default"
        ),
        target=target.runtime if target is not None else None,
        target_root=target.file_root if target is not None else None,
        model_policy=resolve_model_policy(session, environment.id, project_id),
    )


def run_folder(services: ReplyServices, setup: ReplySetup) -> Path | None:
    if setup.agent_project is None:
        return None
    return setup.workspace or project_path(services.workspace_dir, setup.agent_project)


def limit_access(
    session: Session,
    config: RunConfig,
    conversation: Conversation,
    setup: ReplySetup,
    read_only: bool,
    unattended: bool,
) -> RunConfig:
    """The configuration with the limits of a scheduled task, a read-only reply and the target applied."""
    policy = config.policy
    # A scheduled task's run: nobody approves, and the task's own allowlists apply.
    scheduled = active_run(session, conversation.id)
    unattended = unattended or scheduled is not None
    if scheduled is not None:
        _, task = scheduled
        policy = replace(
            policy,
            commands=(*policy.commands, *task.allowed_commands),
            domains=(*policy.domains, *(domain.lower() for domain in task.allowed_domains)),
        )
    if read_only:
        policy = replace(policy, access="read_only")
    # Full access on a computer over SSH only when that target allows it, and never for a scheduled task.
    if setup.target is not None:
        policy = replace(policy, access=allowed_access(policy.access, setup.target.target, unattended))
    return replace(config, policy=policy, unattended=unattended)


def run_services(
    services: ReplyServices,
    session: Session,
    conversation: Conversation,
    setup: ReplySetup,
    allowed_tools: set[str] | None,
    on_step: StepListener | None = None,
) -> RunServices:
    """What the run's tools work with, as far as its place allows: checkpoints, the shared document, search,
    delegation and the goal only in a project; secrets in a project and outside one on a computer. A throwaway
    workspace takes no checkpoints."""
    environment, project = setup.environment, setup.agent_project
    project_id = project.id if project is not None else None
    in_project = project is not None
    # Outside a project on a computer: the secrets of the environment and Global, as placeholders.
    with_secrets = in_project or setup.target is not None
    goal = open_goal(session, conversation.id) if in_project else None
    recorder = secret_use_recorder(services, environment.id, project_id, conversation.id) if with_secrets else None
    extra_tools, catalog = outside_tools(services, session, conversation, setup, allowed_tools)
    # The project's language servers run where its commands run.
    runtime = setup.target.runtime if setup.target is not None else None
    return RunServices(
        web_search=services.web_search,
        searcher=RunSearch(services.session_factory, services.indexer, services.data_dir) if in_project else None,
        checkpoints=(
            CheckpointStore(services.session_factory, services.data_dir)
            if in_project and setup.workspace is None
            else None
        ),
        documents=DocumentLink(services.session_factory, conversation.id) if in_project else None,
        secrets=resolve_secrets(session, services.cipher, environment.id, project_id) if with_secrets else {},
        on_secret_use=recorder,
        shells=services.shells,
        processes=services.processes,
        outputs=OutputStore(services.data_dir, conversation.id),
        extra_tools=extra_tools,
        catalog=catalog,
        steering=QueueSteeringHandoff(services.session_factory),
        injection_check=injection_check(services, session, conversation, setup),
        delegator=delegator(services, conversation, setup) if in_project else None,
        goal=GoalHandle(goal.id, services.session_factory) if goal is not None and goal.status == ACTIVE else None,
        trace=reply_trace(services, on_step),
        code_intel=code_intel_for_run(run_folder(services, setup), runtime) if in_project else None,
    )


def reply_model_trace(
    services: ReplyServices,
    session: Session,
    conversation: Conversation,
    setup: ReplySetup,
    run: AgentRun | None,
    on_step: StepListener | None = None,
) -> ModelTrace:
    """Where the reply's model calls are recorded: under its run, or, on a connection that runs no agents, under a run
    record of its own. Snapshots never hold the values of the secrets the outgoing check knows."""
    if run is not None and run.trace is not None and run.state.record_id is not None:
        recorder, record_id = run.trace, run.state.record_id
    else:
        record_id = start_reply_record(session, conversation, setup.reply_model(conversation)[0])
        recorder = reply_trace(services, on_step)
    pricing = catalog_pricing(services.providers, setup.provider_spec.metered)
    return ModelTrace(recorder, record_id, pricing, setup.outgoing.secrets)


def reply_trace(services: ReplyServices, on_step: StepListener | None = None) -> TraceRecorder:
    """The recorder of a reply's steps, with the context snapshots under data/traces."""
    return TraceRecorder(services.session_factory, SnapshotStore(trace_dir(services.data_dir)), on_step)


def outside_tools(
    services: ReplyServices,
    session: Session,
    conversation: Conversation,
    setup: ReplySetup,
    allowed_tools: set[str] | None,
) -> tuple[dict[str, Tool], ToolCatalog]:
    """Tools of outside MCP servers, remote A2A agents and the browser, and which of them wait until they join the
    run (the browser's until open_browser, a large server's until find_tools); an agent with its own tools list
    keeps to it."""
    environment_id = setup.environment.id
    project = setup.agent_project
    tools: dict[str, Tool] = {}
    mcp = RunMcpTools({})
    if allowed_tools is None:
        project_id = project.id if project is not None else None
        mcp = load_run_tools(services.cipher, services.mcp, session, environment_id, project_id)
        tools.update(mcp.tools)
    # Remote A2A agents of the environment, also for an agent that lists call_agent.
    if allowed_tools is None or A2A_TOOL in allowed_tools:
        factory, transport = services.session_factory, services.a2a_transport
        project_id = project.id if project is not None else None
        tools.update(
            agent_tools(factory, services.cipher, transport, session, environment_id, conversation.id, project_id)
        )
    # The browser: in project conversations, in the environment's runner, also for an agent that lists open_browser.
    # Without it, open_browser is not offered.
    browser: dict[str, Tool] = {}
    if (allowed_tools is None or "open_browser" in allowed_tools) and project is not None and services.browser_command:
        browser = anyio.from_thread.run(
            browser_tools,
            services.mcp,
            services.browser_command,
            services.data_dir,
            conversation.id,
            server_place(environment_id).place,
        )
        tools.update(browser)
    return tools, ToolCatalog(browser=frozenset(browser), servers=mcp.servers)


def injection_check(
    services: ReplyServices, session: Session, conversation: Conversation, setup: ReplySetup
) -> InjectionCheck | None:
    """The optional utility-model check of approvals the injection guard asks (setting injection_check)."""
    project_id = setup.agent_project.id if setup.agent_project is not None else None
    if resolve_setting(session, SETTING_DEFINITIONS["injection_check"], setup.environment.id, project_id).value != "on":
        return None
    return utility_injection_check(services.utility, setup.environment.id, conversation.id)


def delegator(services: ReplyServices, conversation: Conversation, setup: ReplySetup) -> Delegator:
    return Delegator(
        session_factory=services.session_factory,
        cipher=services.cipher,
        credential_types=services.credential_types,
        providers=services.providers,
        runs=services.runs,
        internal_url=services.internal_url,
        layers=setup.layers,
        project_text=setup.project_text,
        languages=setup.languages,
        working_folder=run_folder(services, setup),
        preset=conversation.preset,
        model=conversation.model,
        effort=conversation.effort,
        project_data=setup.project_data,
    )
