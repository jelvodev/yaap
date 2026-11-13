"""Request checks for conversations: the chosen target, full access, the agent and the blueprint layers."""

from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.agents.definitions import find_agent
from app.api.schemas import ConversationTargetOut
from app.blueprint.layers import Layer
from app.blueprint.levels import layers_for
from app.storage.models import Environment, Project, Target


def check_target_choice(session: Session, environment_id: int, target_id: int | None) -> int | None:
    if target_id is None:
        return None
    target = session.get(Target, target_id)
    # A project's sandbox is chosen in the project, not per conversation.
    if target is None or target.environment_id != environment_id or target.kind != "ssh":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown target in this environment.")
    return target.id


def check_full_access(target: ConversationTargetOut | None) -> None:
    if target is not None and not target.allow_full_access:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Full access is off for {target.name}. Switch on Allow full access for it in Settings › Targets first.",
        )


def conversation_layers(
    session: Session, blueprint_dir: Path, workspace_dir: Path, environment_id: int, project_id: int | None
) -> list[Layer]:
    environment = session.get_one(Environment, environment_id)
    project = session.get(Project, project_id) if project_id else None
    return layers_for(blueprint_dir, workspace_dir, environment, project)


def check_agent(layers: list[Layer], name: str | None) -> str | None:
    if name is None or not name.strip():
        return None
    if find_agent(layers, name.strip()) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"There is no agent named {name.strip()}.")
    return name.strip()
