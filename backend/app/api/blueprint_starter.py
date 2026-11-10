"""YAAP's starter Blueprint per level: which packs there are, their links at the current ref and their state.

Installing uses the package endpoints with a pack's link; moving to a newer version uses the package update with
the starter's ref. A router of its own because `api/blueprint.py` is already past the size limit.
"""

from typing import Annotated

import anyio
from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.api.blueprint import LayersDependency, unprocessable
from app.api.dependencies import CipherDependency, SessionDependency
from app.blueprint import starter
from app.blueprint.layers import BlueprintError
from app.blueprint.skill_install import GitHubSkills
from app.github.token import github_token_for
from app.version import VERSION

router = APIRouter(prefix="/blueprint", tags=["blueprint"])


class StarterPackOut(BaseModel):
    key: str
    # The package name, as installed packages and the update endpoints know it.
    name: str
    title: str
    description: str
    # Chosen when the starter is first offered.
    chosen: bool
    # The pack's folder on GitHub at the current ref, to preview and install it with.
    url: str
    installed: bool
    # The ref this level installed the pack from.
    installed_ref: str | None
    # Installed from another ref than the current one: an update with `ref` moves it.
    outdated: bool


class StarterOut(BaseModel):
    repository: str
    version: str
    # The tag of the running version, or the development branch when that tag does not exist.
    ref: str
    pinned_to_version: bool
    packs: list[StarterPackOut]
    # Where each starter item comes from, at the current ref.
    credits_url: str


def pack_out(state: starter.PackState) -> StarterPackOut:
    return StarterPackOut(
        key=state.pack.key,
        name=state.pack.name,
        title=state.pack.title,
        description=state.pack.description,
        chosen=state.pack.chosen,
        url=state.url,
        installed=state.installed,
        installed_ref=state.installed_ref,
        outdated=state.outdated,
    )


@router.get("/starter", response_model=StarterOut)
async def read_starter(
    layers: LayersDependency,
    session: SessionDependency,
    cipher: CipherDependency,
    environment_id: Annotated[int | None, Query()] = None,
) -> StarterOut:
    """The starter's packs at the ref of the running version, and which of them this level has; nothing is written."""
    github = GitHubSkills(github_token_for(session, cipher, environment_id))
    try:
        current = await anyio.to_thread.run_sync(starter.starter_ref, github, VERSION)
    except BlueprintError as error:
        raise unprocessable(error) from error
    return StarterOut(
        repository=starter.STARTER_REPOSITORY,
        version=VERSION,
        ref=current.ref,
        pinned_to_version=current.pinned_to_version,
        packs=[pack_out(state) for state in starter.pack_states(layers[-1], current.ref)],
        credits_url=starter.credits_url(current.ref),
    )
