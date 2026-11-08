"""Agents screen: the environment's agent definitions, starter templates and recent runs."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.agents.definitions import AGENT_TEMPLATES, AgentDefinition, agent_definitions
from app.agents.records import RunPage, list_run_records
from app.api.dependencies import (
    EnvironmentDependency,
    BlueprintDirDependency,
    SessionDependency,
    WorkspaceDirDependency,
)
from app.agent.tools import TOOLS
from app.api.schemas import AgentFromTemplate, AgentOut, AgentTemplateOut, AgentToolOut, RunApprovalOut, RunOut
from app.blueprint.layers import BlueprintError, parse_document, write_file
from app.blueprint.levels import environment_layer, layers_for
from app.storage.models import Project

router = APIRouter(tags=["agents"])

RECENT_RUNS = 50
MAX_RUNS_PAGE = 200


def agent_out(agent: AgentDefinition) -> AgentOut:
    return AgentOut(
        name=agent.name,
        description=agent.description,
        preset=agent.preset,
        role=agent.role,
        tools=sorted(agent.tools) if agent.tools is not None else None,
        skills=list(agent.skills),
        commands=list(agent.commands) if agent.commands is not None else None,
        source_label=agent.source_label,
        path=agent.path,
        warnings=list(agent.warnings),
    )


@router.get("/environments/{environment_id}/agents", response_model=list[AgentOut])
def list_agents(
    environment: EnvironmentDependency,
    session: SessionDependency,
    blueprint_dir: BlueprintDirDependency,
    workspace_dir: WorkspaceDirDependency,
    project_id: int | None = None,
) -> list[AgentOut]:
    """The active agents of the environment, or of a project in it (its own layer included)."""
    project = session.get(Project, project_id) if project_id else None
    if project is not None and project.environment_id != environment.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found in this environment.")
    layers = layers_for(blueprint_dir, workspace_dir, environment, project)
    return [agent_out(agent) for agent in agent_definitions(layers)]


@router.get("/agent-tools", response_model=list[AgentToolOut])
def list_agent_tools() -> list[AgentToolOut]:
    """Every tool an agent can be given, to show and group an agent's tools."""
    return [
        AgentToolOut(
            name=tool.name,
            description=tool.description.split(". ")[0].rstrip("."),
            read_only=tool.read_only,
            permission=tool.permission,
        )
        for tool in TOOLS
    ]


@router.get("/agent-templates", response_model=list[AgentTemplateOut])
def list_agent_templates() -> list[AgentTemplateOut]:
    return [
        AgentTemplateOut(name=name, description=str(parse_document(text).front_matter.get("description", "")))
        for name, text in AGENT_TEMPLATES.items()
    ]


@router.post(
    "/environments/{environment_id}/agents/from-template",
    response_model=AgentOut,
    status_code=status.HTTP_201_CREATED,
)
def add_agent_from_template(
    payload: AgentFromTemplate,
    environment: EnvironmentDependency,
    session: SessionDependency,
    blueprint_dir: BlueprintDirDependency,
    workspace_dir: WorkspaceDirDependency,
) -> AgentOut:
    """Write a starter agent into the environment's Blueprint layer, where it can be edited like any item."""
    template = AGENT_TEMPLATES.get(payload.template)
    if template is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown agent template.")
    layer = environment_layer(blueprint_dir, environment)
    path = f"agents/{payload.template}.md"
    if (layer.root / path).exists():
        raise HTTPException(status.HTTP_409_CONFLICT, f"{environment.name} already has an agent named {payload.template}.")
    try:
        write_file(layer, path, template, f"Add the {payload.template} agent")
    except BlueprintError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    layers = layers_for(blueprint_dir, workspace_dir, environment, None)
    created = next(agent for agent in agent_definitions(layers) if agent.name == payload.template)
    return agent_out(created)


@router.get("/environments/{environment_id}/runs", response_model=list[RunOut])
def list_runs(
    environment: EnvironmentDependency,
    session: SessionDependency,
    agent: Annotated[str | None, Query(max_length=100)] = None,
    sub_runs: bool = True,
    before: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_RUNS_PAGE)] = RECENT_RUNS,
) -> list[RunOut]:
    """The agent runs of the environment, newest first, a page at a time; running ones show status "running".

    With `agent`, only runs of that agent and the sub-runs they started, so the run tree stays whole. With
    `sub_runs=false`, only the runs themselves (a sub-run is part of its run's trace); with an agent, its runs at any
    depth. The next page starts `before` the id of the last run of this one; a page shorter than `limit` is the last.
    """
    page = RunPage(agent=agent or None, sub_runs=sub_runs, before=before, limit=limit)
    rows = list_run_records(session, environment.id, page)
    return [
        RunOut(
            id=record.id,
            conversation_id=record.conversation_id,
            conversation_title=title,
            project_id=record.project_id,
            project_name=project_name,
            agent=record.agent,
            model=record.model,
            status=record.status,
            tool_calls=record.tool_calls,
            approvals=[RunApprovalOut(**approval) for approval in record.approvals or []],
            started_at=record.started_at,
            finished_at=record.finished_at,
            parent_run_id=record.parent_run_id,
            task=record.task,
            transcript=record.transcript or [],
            worker=record.worker,
        )
        for record, title, project_name in rows
    ]
