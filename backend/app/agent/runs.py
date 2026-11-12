"""Agent runs that are in progress, and the approvals they wait for.

A run gets a secret token. The token is given only to the model process for that run (in its MCP config) and binds
every tool call to one conversation, its environment, its project folder (if any) and its attachments. Events for the
user interface (approval requests) go to the run's queue, which the chat stream reads alongside the model's events.

A run has three parts: its `RunConfig` (who, where, which tools and what may run without asking; fixed,
except the access level the user changes in the chat), its `RunServices` (what its tools work with; fixed) and its
`RunState` (what changes while it works). The fixed parts read as properties of the run (`run.project_folder`,
`run.secrets`); what changes is written and read through `run.state`. `RunRegistry.child` makes a delegated sub-run
from its parent with exactly what it inherits.
"""

import asyncio
import secrets
import stat
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from app.agent.outputs import OutputStore
from app.agent.policy import AccessLevel, ToolPolicy
from app.agent.tool_catalog import ToolCatalog
from app.blueprint.layers import Layer
from app.checkpoints.service import CheckpointStore
from app.core.goals import GoalHandle
from app.core.model_policy import ModelPolicy
from app.plans.service import PlanService
from app.core.search import RunSearch
from app.core.why import Why
from app.targets.ssh import SshTarget
from app.tracing.recorder import TraceRecorder
from app.websearch.service import SearchProvider

if TYPE_CHECKING:
    # These modules use AgentRun themselves (their tools and handlers take a run), so they are imported for the
    # annotations only.
    from app.agent.delegation import Delegator
    from app.board.intake import CardIntake
    from app.board.linked_card import LinkedCard
    from app.agent.processes import ProcessManager
    from app.agent.shells import ShellSessions
    from app.agent.toolkit import Tool
    from app.documents.service import DocumentLink
    from app.lsp.service import CodeIntel

APPROVAL_TIMEOUT_SECONDS = 30 * 60
Decision = Literal["approve", "deny"]
# An event for the chat (an approval card, live output, a checkpoint): a JSON object with its kind under "event".
RunEvent = dict[str, object]
# Records in the vault's log that secrets were filled in: called with their names and what used them.
SecretUseRecorder = Callable[[list[str], str], None]


@dataclass
class PendingApproval:
    id: str
    run_token: str
    tool: str
    summary: str
    detail: str
    future: asyncio.Future[Decision]
    # Why a tool that normally runs by itself asks (the injection guard), and what the optional check found.
    reason: str | None = None
    suspicion: str | None = None
    # Why the user declined, when they wrote it in the approval card.
    answer_reason: str | None = None


@dataclass(frozen=True)
class ApprovalAnswer:
    """The user's decision on an approval, with the reason they gave for it (optional)."""

    decision: Decision
    reason: str | None = None


@dataclass(frozen=True)
class OutsideSource:
    """Outside content a run has read: a web page, an attachment, an installed skill."""

    label: str
    excerpt: str


# (summary, arguments, outside excerpts) -> why the call looks suspicious, or None.
InjectionCheck = Callable[[str, str, str], Awaitable[str | None]]


@dataclass(frozen=True)
class AttachmentRef:
    """An attachment the run's tools may read: its stored folder and how it was recognised."""

    id: int
    name: str
    kind: str
    media_type: str
    folder: Path


@dataclass(frozen=True)
class RunConfig:
    """Who the run works for, where, with which tools, and what may run without asking."""

    conversation_id: int
    environment_id: int
    blueprint_layers: list[Layer]
    # The tools this run may call: what fits the conversation and its agent.
    tool_names: frozenset[str]
    project_id: int | None = None
    project_folder: Path | None = None
    attachments: list[AttachmentRef] = field(default_factory=list)
    git_author: tuple[str, str] | None = None
    github_token: str | None = None
    agent: str | None = None
    # What may run without asking: per-tool choices and allowlists from the settings, and the access level.
    policy: ToolPolicy = field(default_factory=ToolPolicy)
    # A scheduled task's run: nobody is there to approve, so what would ask is refused.
    unattended: bool = False
    # Where the tools work; None is the container. A computer over SSH, a sandbox or the runner comes with
    # the working folder there, as that computer writes it.
    target: SshTarget | None = None
    target_root: str | None = None
    # A delegated sub-run: the token of the run that started it, and who it is in approval cards.
    parent_token: str | None = None
    label: str | None = None
    # The model policy of the environment and the project: the A2A agents call_agent may reach and the
    # patterns that block a tool's output before it goes back to the model.
    model_policy: ModelPolicy = field(default_factory=ModelPolicy)

    def for_child(
        self, parent_token: str, label: str, agent: str | None, allowed_tools: frozenset[str] | None
    ) -> "RunConfig":
        """A sub-run works for the same conversation, in the same place, under the same policy; it cannot delegate."""
        tool_names = self.tool_names - {"delegate"}
        if allowed_tools is not None:
            tool_names &= allowed_tools
        return replace(self, tool_names=tool_names, agent=agent, parent_token=parent_token, label=label)


@dataclass(frozen=True)
class RunServices:
    """What the run's tools work with. A service a run cannot use (outside a project, without the app) is None."""

    # Events for the chat; a sub-run's go to the conversation of the run that started it.
    events: asyncio.Queue[RunEvent] = field(default_factory=asyncio.Queue)
    # The SearXNG service, when YAAP has its address.
    web_search: SearchProvider | None = None
    # Semantic search of conversations, memory and knowledge (search_project, read_knowledge).
    searcher: RunSearch | None = None
    # Taken before each tool call that can change project files.
    checkpoints: CheckpointStore | None = None
    # The conversation's shared document.
    documents: "DocumentLink | None" = None
    # Secret placeholders: name -> value, inserted into commands and masked in every output.
    secrets: dict[str, str] = field(default_factory=dict)
    on_secret_use: SecretUseRecorder | None = None
    # The shell sessions of run_command, kept per conversation, and the app's background processes.
    shells: "ShellSessions | None" = None
    processes: "ProcessManager | None" = None
    # Long outputs are kept whole here and read back with read_output.
    outputs: OutputStore | None = None
    # Tools of outside MCP servers, remote A2A agents and the agent's browser, by the name the model sees.
    extra_tools: dict[str, "Tool"] = field(default_factory=dict)
    # Which extra tools are offered only after they join the run (the browser's after open_browser, a large outside MCP
    # server's after find_tools), and the outside MCP servers the prompt lists.
    catalog: ToolCatalog = field(default_factory=ToolCatalog)
    steering: "SteeringHandoff | None" = None
    # The optional utility-model check of approvals asked by the injection guard (setting injection_check).
    injection_check: InjectionCheck | None = None
    # Starts sub-runs (delegate); only a top-level run in a project has one.
    delegator: "Delegator | None" = None
    # The open goal of the conversation, which finish_goal ends.
    goal: GoalHandle | None = None
    # Records the run's model and tool calls as steps; a sub-run shares it and records under its own run record.
    trace: TraceRecorder | None = None
    # The project's language servers, where a server for one of its languages can run (the code tools).
    code_intel: "CodeIntel | None" = None
    # Where submit_plan stores a plan; only a top-level run in a project has it.
    plans: PlanService | None = None
    # The card the conversation belongs to, for the card tools; only a top-level run while the board is on has it.
    card: "LinkedCard | None" = None
    # Where add_card puts cards; only a top-level run in a project whose board is on and started has it.
    intake: "CardIntake | None" = None

    def for_child(self) -> "RunServices":
        """A sub-run uses its parent's services, but cannot delegate, end the conversation's goal, hand over a plan
        or change or add cards."""
        return replace(self, delegator=None, goal=None, plans=None, card=None, intake=None)


@dataclass
class RunState:
    """What changes while the run works."""

    # The stored record of this run (agent_runs), updated when the run ends.
    record_id: int | None = None
    # Loop detection: the last tool call's key and how often it came in a row.
    last_call: str | None = None
    repeats: int = 0
    approvals: dict[str, PendingApproval] = field(default_factory=dict)
    # Every approval asked during the run, for the record.
    approval_log: list[dict[str, str]] = field(default_factory=list)
    # Hash per project file of the version the run last read or wrote (no edits on outdated files).
    file_hashes: dict[str, str] = field(default_factory=dict)
    # Outside content read in this run, and project files that came from outside (imported attachments).
    outside_sources: list[OutsideSource] = field(default_factory=list)
    outside_files: set[str] = field(default_factory=set)
    # Project files this run wrote with write_file or edit_file, for a sub-run's report.
    changed_files: set[str] = field(default_factory=set)
    # Messages the user sent while the run works, delivered with the next tool result.
    steering: list[str] = field(default_factory=list)
    steering_item_ids: list[int] = field(default_factory=list)
    # Extra tools held back by the catalog that have joined the run; offered from the next model call on.
    joined_tools: set[str] = field(default_factory=set)
    # How often each test command failed in this run (app/learning signals).
    failed_tests: dict[str, int] = field(default_factory=dict)
    # The context_savings setting, read once when the run starts (app/core/context_savings.py).
    context_savings: bool = False
    # Full reads of read_file in this run: (path, first line, last line) -> hash of the result and the read's number.
    read_ranges: dict[tuple[str, int, int], tuple[str, int]] = field(default_factory=dict)
    reads: int = 0

    def for_child(self, record_id: int | None) -> "RunState":
        """A sub-run shares the injection guard's state with its parent, both ways, and starts with the tools its
        parent had joined (a copy: what the sub-run joins stays its own); the rest starts empty."""
        return RunState(
            record_id=record_id,
            outside_sources=self.outside_sources,
            outside_files=self.outside_files,
            joined_tools=set(self.joined_tools),
            context_savings=self.context_savings,
        )


class SteeringHandoff(Protocol):
    def claim(self, conversation_id: int, run_id: int) -> list[tuple[int, str]]: ...
    def acknowledge(self, run_id: int, item_ids: list[int]) -> None: ...
    def restore(self, run_id: int) -> None: ...


@dataclass
class AgentRun:
    token: str
    config: RunConfig
    services: RunServices = field(default_factory=RunServices)
    state: RunState = field(default_factory=RunState)
    project_identity: tuple[int, int] | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        """Remember the project directory object when the run starts, so later path replacement is detectable."""
        folder = self.config.project_folder
        if folder is None:
            return
        try:
            info = folder.lstat()
        except OSError:
            return
        if stat.S_ISDIR(info.st_mode):
            self.project_identity = (info.st_dev, info.st_ino)

    def change_access(self, access: AccessLevel) -> None:
        """The user changed the conversation's access level: it applies from the next tool call."""
        self.config = replace(self.config, policy=replace(self.config.policy, access=access))

    # The configuration, read-only.

    @property
    def conversation_id(self) -> int:
        return self.config.conversation_id

    @property
    def environment_id(self) -> int:
        return self.config.environment_id

    @property
    def blueprint_layers(self) -> list[Layer]:
        return self.config.blueprint_layers

    @property
    def tool_names(self) -> frozenset[str]:
        return self.config.tool_names

    @property
    def project_id(self) -> int | None:
        return self.config.project_id

    @property
    def project_folder(self) -> Path | None:
        return self.config.project_folder

    @property
    def attachments(self) -> list[AttachmentRef]:
        return self.config.attachments

    @property
    def git_author(self) -> tuple[str, str] | None:
        return self.config.git_author

    @property
    def github_token(self) -> str | None:
        return self.config.github_token

    @property
    def agent(self) -> str | None:
        return self.config.agent

    @property
    def policy(self) -> ToolPolicy:
        return self.config.policy

    @property
    def unattended(self) -> bool:
        return self.config.unattended

    @property
    def model_policy(self) -> ModelPolicy:
        return self.config.model_policy

    @property
    def target(self) -> SshTarget | None:
        return self.config.target

    @property
    def target_root(self) -> str | None:
        return self.config.target_root

    @property
    def parent_token(self) -> str | None:
        return self.config.parent_token

    @property
    def label(self) -> str | None:
        return self.config.label

    # The services, read-only.

    @property
    def events(self) -> asyncio.Queue[RunEvent]:
        return self.services.events

    @property
    def web_search(self) -> SearchProvider | None:
        return self.services.web_search

    @property
    def searcher(self) -> RunSearch | None:
        return self.services.searcher

    @property
    def checkpoints(self) -> CheckpointStore | None:
        return self.services.checkpoints

    @property
    def documents(self) -> "DocumentLink | None":
        return self.services.documents

    @property
    def secrets(self) -> dict[str, str]:
        return self.services.secrets

    @property
    def on_secret_use(self) -> SecretUseRecorder | None:
        return self.services.on_secret_use

    @property
    def shells(self) -> "ShellSessions | None":
        return self.services.shells

    @property
    def processes(self) -> "ProcessManager | None":
        return self.services.processes

    @property
    def outputs(self) -> OutputStore | None:
        return self.services.outputs

    @property
    def extra_tools(self) -> dict[str, "Tool"]:
        return self.services.extra_tools

    @property
    def catalog(self) -> ToolCatalog:
        return self.services.catalog

    @property
    def browser_on_demand(self) -> frozenset[str]:
        """The browser's tools that open_browser adds; empty where the run has no browser."""
        return self.services.catalog.browser

    @property
    def mcp_behind_search(self) -> frozenset[str]:
        """The outside MCP tools that find_tools adds; empty when every server is fully listed."""
        return self.services.catalog.searchable

    @property
    def injection_check(self) -> InjectionCheck | None:
        return self.services.injection_check

    @property
    def delegator(self) -> "Delegator | None":
        return self.services.delegator

    @property
    def goal(self) -> GoalHandle | None:
        return self.services.goal

    @property
    def plans(self) -> PlanService | None:
        return self.services.plans

    @property
    def card(self) -> "LinkedCard | None":
        return self.services.card

    @property
    def intake(self) -> "CardIntake | None":
        return self.services.intake

    @property
    def trace(self) -> TraceRecorder | None:
        return self.services.trace

    @property
    def code_intel(self) -> "CodeIntel | None":
        return self.services.code_intel


class RunRegistry:
    def __init__(self) -> None:
        self.runs: dict[str, AgentRun] = {}

    def start(self, config: RunConfig, services: RunServices | None = None) -> AgentRun:
        return self.add(AgentRun(secrets.token_urlsafe(32), config, services or RunServices()))

    def child(
        self,
        parent: AgentRun,
        label: str,
        record_id: int | None,
        agent: str | None = None,
        allowed_tools: frozenset[str] | None = None,
    ) -> AgentRun:
        """A delegated sub-run of `parent`: its own token, label, agent and state, the parent's tools without
        `delegate` (narrowed to the agent's `allowed_tools`), and everything else of the parent."""
        token = secrets.token_urlsafe(32)
        child = AgentRun(
            token,
            parent.config.for_child(parent.token, label, agent, allowed_tools),
            parent.services.for_child(),
            parent.state.for_child(record_id),
        )
        child.project_identity = parent.project_identity
        return self.add(child)

    def add(self, run: AgentRun) -> AgentRun:
        self.runs[run.token] = run
        return run

    def find(self, token: str) -> AgentRun | None:
        return self.runs.get(token)

    def for_conversation(self, conversation_id: int) -> AgentRun | None:
        """The running top-level run of a conversation (not a delegated sub-run)."""
        return next(
            (run for run in self.runs.values() if run.conversation_id == conversation_id and run.parent_token is None),
            None,
        )

    def finish(self, run: AgentRun) -> None:
        """End the run: refuse further tool calls and deny whatever still waits for a decision."""
        self.runs.pop(run.token, None)
        for approval in run.state.approvals.values():
            if not approval.future.done():
                approval.future.set_result("deny")

    def find_approval(self, approval_id: str) -> tuple[AgentRun, PendingApproval] | None:
        for run in self.runs.values():
            approval = run.state.approvals.get(approval_id)
            if approval is not None:
                return run, approval
        return None

    async def request_approval(
        self,
        run: AgentRun,
        tool: str,
        summary: str,
        detail: str,
        reason: str | None = None,
        suspicion: str | None = None,
        why: Why | None = None,
    ) -> ApprovalAnswer:
        return await ask_approval_answer(run, tool, summary, detail, reason, suspicion, why)

    def decide(self, approval_id: str, decision: Decision, reason: str | None = None) -> bool:
        """The user's decision, with the reason they gave (kept on the tool's step as a learning signal)."""
        found = self.find_approval(approval_id)
        if found is None:
            return False
        _, approval = found
        if not approval.future.done():
            approval.answer_reason = (" ".join(reason.split())[:500] or None) if reason else None
            approval.future.set_result(decision)
        return True


async def ask_approval(
    run: AgentRun, tool: str, summary: str, detail: str, reason: str | None = None, suspicion: str | None = None
) -> Decision:
    """Show an approval card in the run's chat and wait for the user's decision (denied after 30 minutes).

    Tools whose handler must ask itself (install_skill) call this directly, with the review as the detail.
    """
    return (await ask_approval_answer(run, tool, summary, detail, reason, suspicion)).decision


async def ask_approval_answer(
    run: AgentRun,
    tool: str,
    summary: str,
    detail: str,
    reason: str | None = None,
    suspicion: str | None = None,
    why: Why | None = None,
) -> ApprovalAnswer:
    """ask_approval with the reason the user gave for their decision."""
    if run.unattended:
        return ApprovalAnswer("deny")
    if run.parent_token is not None and run.label:
        summary = f"[{run.label}] {summary}"  # A sub-run's approval card names who asks.
    approval = PendingApproval(
        id=secrets.token_urlsafe(12),
        run_token=run.token,
        tool=tool,
        summary=summary,
        detail=detail,
        future=asyncio.get_running_loop().create_future(),
        reason=reason,
        suspicion=suspicion,
    )
    run.state.approvals[approval.id] = approval
    await run.events.put(
        {
            "event": "approval_request",
            "approval_id": approval.id,
            "tool": tool,
            "summary": summary,
            "detail": detail,
            "reason": reason,
            "suspicion": suspicion,
            "why": why.stored() if why else None,
        }
    )
    try:
        decision = await asyncio.wait_for(asyncio.shield(approval.future), timeout=APPROVAL_TIMEOUT_SECONDS)
    except TimeoutError:
        decision = "deny"
    run.state.approvals.pop(approval.id, None)
    run.state.approval_log.append(
        {
            "tool": tool,
            "summary": summary,
            "decision": decision,
            "decided_at": datetime.now(UTC).isoformat(),
            **({"reason": reason} if reason else {}),
            **({"suspicion": suspicion} if suspicion else {}),
            **({"answer": approval.answer_reason} if approval.answer_reason else {}),
        }
    )
    await run.events.put({"event": "approval_resolved", "approval_id": approval.id, "decision": decision})
    return ApprovalAnswer(decision, approval.answer_reason)
