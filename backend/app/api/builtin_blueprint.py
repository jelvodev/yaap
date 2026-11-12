"""The built-in Blueprint items of an environment: what YAAP's helpers are told, who replaced them, their cost."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.dependencies import (
    BlueprintDirDependency,
    EnvironmentDependency,
    ProviderRegistryDependency,
    SessionDependency,
    WorkspaceDirDependency,
)
from app.core.background_tasks import builtin_rows

router = APIRouter(tags=["blueprint"])


class OverriddenByOut(BaseModel):
    kind: Literal["global", "environment", "project"]
    label: str


class BuiltinItemOut(BaseModel):
    name: str
    kind: Literal["agent", "skill"]
    path: str
    description: str
    when: str
    role: Literal["worker", "explorer"] | None
    tasks: list[str]
    instructions: str
    can_override: bool
    overridden_by: OverriddenByOut | None
    # The model the item runs on; None when no single model applies, then model_note says which does.
    model: str | None
    model_note: str | None
    # This month; None when the item's runs are not recorded apart.
    runs: int | None
    cost: float | None


class BuiltinItemsOut(BaseModel):
    items: list[BuiltinItemOut]


@router.get("/environments/{environment_id}/blueprint/builtin", response_model=BuiltinItemsOut)
def environment_builtin_items(
    environment: EnvironmentDependency,
    session: SessionDependency,
    providers: ProviderRegistryDependency,
    blueprint_dir: BlueprintDirDependency,
    workspace_dir: WorkspaceDirDependency,
) -> BuiltinItemsOut:
    rows = builtin_rows(session, providers, environment, blueprint_dir, workspace_dir)
    return BuiltinItemsOut(items=[BuiltinItemOut.model_validate(row, from_attributes=True) for row in rows])
