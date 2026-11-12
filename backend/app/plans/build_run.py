"""The build's own run: the parent of every step's sub-runs, with no model calls of its own.

It is started as a reply's run is (core/reply_run.start_agent_run), in the plan's conversation, with the build's copy
as the run's workspace: its tools, the sub-runs' tools and the checks YAAP runs all work in the copy. It owns the
permission gate, the trace and the approvals; its policy adds the plan's check commands, which the user approved
with the plan (agent/policy.build_command_allowed). Approvals it or a step asks for wait in the run as in a chat.
"""

from dataclasses import dataclass, replace
from pathlib import Path

from sqlalchemy.orm import Session

from app.agent.delegation import Delegator
from app.agent.escalation import Role
from app.agent.runs import AgentRun
from app.agents.records import finish_record
from app.core.reply_errors import ReplyError
from app.core.reply_run import start_agent_run
from app.core.reply_services import ReplyServices
from app.core.reply_setup import prepare_reply
from app.core.settings import SETTING_DEFINITIONS, resolve_setting
from app.plans.build import run_checks
from app.plans.model import Plan
from app.plans.service import BuildRefused, PlanView
from app.projects.service import project_path
from app.storage.models import AgentRunRecord, Conversation, Project
from app.targets.service import RunTarget


@dataclass
class BuildRun:
    """A started build run and what the runner needs beside it."""

    run: AgentRun
    delegator: Delegator
    environment_id: int
    project_folder: Path
    copy_folder: Path
    # Who takes a step with a check first (setting build_worker).
    first: Role


def plan_commands(plan: Plan) -> tuple[str, ...]:
    """Every check command of the plan, once each, in order: what approving the plan approves."""
    return tuple(dict.fromkeys(command for step in plan.steps for command in run_checks(step)))


def copy_target(target: RunTarget | None, build_folder: str) -> RunTarget | None:
    """The project's target with the copy as its working folder. The copy lies inside the project folder, so a
    sandbox and the runner, which work on the project folder in YAAP, reach it; a computer over SSH does not."""
    if target is None:
        return None
    if not target.has_container_tools:
        raise BuildRefused(
            "build_unavailable", "Builds work for projects in YAAP's workspace, not on a computer over SSH."
        )
    return replace(target, root=f"{target.root.rstrip('/')}/{build_folder}")


def build_worker(session: Session, environment_id: int, project_id: int) -> Role:
    value = resolve_setting(session, SETTING_DEFINITIONS["build_worker"], environment_id, project_id).value
    return "main" if value == "main" else "worker"


def open_build_run(services: ReplyServices, record: PlanView) -> BuildRun:
    """Start the build run of the plan in its conversation. Blocking: call it from a worker thread."""
    if not record.build_folder:
        raise BuildRefused("not_resumable", f"Plan {record.id} has no build folder.")
    with services.session_factory() as session:
        conversation = session.get_one(Conversation, record.conversation_id)
        project = session.get_one(Project, record.project_id)
        try:
            setup = prepare_reply(services, session, conversation)
        except ReplyError as error:
            raise BuildRefused("build_unavailable", str(error)) from error
        if setup.agent_project is None:
            raise BuildRefused(
                "build_unavailable", "This conversation's connection cannot run YAAP's tools, so it cannot build."
            )
        folder = project_path(services.workspace_dir, project)
        copy = folder / record.build_folder
        setup = replace(setup, workspace=copy, target=copy_target(setup.target, record.build_folder))
        run = start_agent_run(services, session, conversation, setup, [], read_only=False, unattended=False)
        commands = plan_commands(record.plan)
        run.config = replace(run.config, policy=replace(run.policy, build_commands=commands))
        if run.state.record_id is not None:
            stored = session.get(AgentRunRecord, run.state.record_id)
            if stored is not None:
                stored.task = f"build of plan {record.id}"
                session.commit()
        delegator = run.delegator
        if delegator is None:  # Not reached: a run in a project always has one.
            services.runs.finish(run)
            raise BuildRefused("build_unavailable", "This run cannot hand steps to a worker.")
        first = build_worker(session, project.environment_id, project.id)
    return BuildRun(run, delegator, project.environment_id, folder, copy, first)


def close_build_run(services: ReplyServices, build: BuildRun, status: str) -> None:
    """End the build run: no further tool calls, waiting approvals denied, the record finished."""
    services.runs.finish(build.run)
    record_id = build.run.state.record_id
    if record_id is not None:
        finish_record(services.session_factory, record_id, None, status, 0, build.run.state.approval_log)
