"""The Blueprint per level: list items with where they come from, read, write, switch off and reset.

A level is global (no environment), an environment, or a project in an environment.
"""

from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.blueprint_mapping import (
    candidate_out,
    item_to_out,
    mutation_status,
    package_index,
    package_item_out,
    source_out,
)
from app.api.blueprint_schemas import (
    BlueprintFileOut,
    BlueprintFileWrite,
    BlueprintItemOut,
    GitHubInstallIn,
    GitHubPreviewIn,
    GitHubPreviewOut,
    InstalledPackageOut,
    InstalledSkillOut,
    InstalledSkillsOut,
    MutationStatusesOut,
    MutationStatusOut,
    PackageAcceptIn,
    PackageInfoOut,
    PackageInstalledItemOut,
    PackageInstallIn,
    PackageInstallOut,
    PackageItemUpdateOut,
    PackagePreviewOut,
    PackageUpdateOut,
    RuleIn,
    RuleOut,
    SkillUpdateOut,
)
from app.api.dependencies import BlueprintDirDependency, CipherDependency, SessionDependency, WorkspaceDirDependency
from app.blueprint import operations, package_updates, packages, skill_install
from app.blueprint.errors import BlueprintConflictError, BlueprintNotFoundError
from app.blueprint.layers import BlueprintError, Layer, merge_layers, read_layer_text, validate_path
from app.blueprint.levels import layers_for
from app.blueprint.skill_install import GitHubSkills
from app.github.token import github_token_for
from app.storage.models import Environment, Project

router = APIRouter(prefix="/blueprint", tags=["blueprint"])

# The names the sibling Blueprint routers import from here.
__all__ = ["LayersDependency", "MutationStatusOut", "mutation_status", "router", "unprocessable"]


def resolve_layers(
    session: SessionDependency,
    blueprint_dir: BlueprintDirDependency,
    workspace_dir: WorkspaceDirDependency,
    environment_id: Annotated[int | None, Query()] = None,
    project_id: Annotated[int | None, Query()] = None,
) -> list[Layer]:
    environment = session.get(Environment, environment_id) if environment_id is not None else None
    if environment_id is not None and environment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Environment not found.")
    project = None
    if project_id is not None:
        project = session.get(Project, project_id)
        if project is None or environment is None or project.environment_id != environment.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found in this environment.")
    return layers_for(blueprint_dir, workspace_dir, environment, project)


LayersDependency = Annotated[list[Layer], Depends(resolve_layers)]


def checked_path(path: str) -> str:
    try:
        return validate_path(path)
    except BlueprintError as error:
        raise unprocessable(error) from error


def unprocessable(error: BlueprintError) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error))


def not_found(error: BlueprintNotFoundError) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, str(error))


@router.get("/items", response_model=list[BlueprintItemOut])
def list_items(layers: LayersDependency) -> list[BlueprintItemOut]:
    index = package_index(layers)
    return [item_to_out(item, layers[-1], index) for item in merge_layers(layers)]


@router.get("/file", response_model=BlueprintFileOut)
def read_file(layers: LayersDependency, path: Annotated[str, Query()]) -> BlueprintFileOut:
    item = operations.find_item(layers, checked_path(path))
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No Blueprint file at this path.")
    own = layers[-1]
    if any(layer == own for layer, _ in item.extensions):
        content = read_layer_text(own, item.path, errors="replace")
        inherited = item.without_layer(own).text()
        return BlueprintFileOut(**item_to_out(item, own).model_dump(), content=content, inherited_content=inherited)
    content = read_layer_text(item.source, item.path, errors="replace")
    # An inherited skill this level could add to: the full text it would add to, additions of levels above included.
    inherited = item.text() if item.kind == "skill" and item.source != own and not item.orphan_extension else None
    return BlueprintFileOut(**item_to_out(item, own).model_dump(), content=content, inherited_content=inherited)


@router.put("/file", response_model=BlueprintFileOut)
def save_file(
    layers: LayersDependency, path: Annotated[str, Query()], payload: BlueprintFileWrite
) -> BlueprintFileOut:
    """Create the file at this level, or override an inherited one."""
    try:
        result = operations.save_file(layers[-1], path, payload.content)
    except BlueprintError as error:
        raise unprocessable(error) from error
    output = read_file(layers, path)
    output.mutation = mutation_status(path, result)
    return output


@router.post("/switch-off", response_model=BlueprintItemOut)
def switch_off(layers: LayersDependency, path: Annotated[str, Query()]) -> BlueprintItemOut:
    """Switch an inherited item off for this level and below, without touching the level that defines it."""
    try:
        switched, result = operations.switch_off(layers, path)
    except BlueprintConflictError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except BlueprintError as error:
        raise unprocessable(error) from error
    output = item_to_out(switched, layers[-1])
    output.mutation = mutation_status(path, result)
    return output


@router.delete("/file", response_model=MutationStatusesOut)
def remove_file(layers: LayersDependency, path: Annotated[str, Query()]) -> MutationStatusesOut:
    """Remove this level's file: an override or switch-off is reset to inherited; an own item is deleted."""
    try:
        removed = operations.remove_file(layers[-1], checked_path(path))
    except BlueprintError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    statuses = [mutation_status(path, removed.file)]
    if removed.package_record is not None:
        statuses.append(mutation_status("packages/*.json", removed.package_record))
    return MutationStatusesOut(statuses=statuses)


# --- Skills from GitHub --------------------------------------------------------------------


@router.post("/skills/github/preview", response_model=GitHubPreviewOut)
async def preview_github_skills(
    payload: GitHubPreviewIn,
    layers: LayersDependency,
    session: SessionDependency,
    cipher: CipherDependency,
    environment_id: Annotated[int | None, Query()] = None,
) -> GitHubPreviewOut:
    """Resolve a GitHub link to a fixed commit and list the skills found there; nothing is written."""
    github = GitHubSkills(github_token_for(session, cipher, environment_id))
    try:
        source, candidates = await anyio.to_thread.run_sync(skill_install.preview, github, payload.url)
    except BlueprintError as error:
        raise unprocessable(error) from error
    return GitHubPreviewOut(
        source=source_out(source), candidates=[candidate_out(candidate, layers[-1]) for candidate in candidates]
    )


@router.post("/skills/github/install", response_model=InstalledSkillsOut)
async def install_github_skills(
    payload: GitHubInstallIn,
    layers: LayersDependency,
    session: SessionDependency,
    cipher: CipherDependency,
    environment_id: Annotated[int | None, Query()] = None,
) -> InstalledSkillsOut:
    """Install the reviewed skills at the reviewed commit into this level, one commit per skill."""
    try:
        operations.check_distinct_names([choice.name for choice in payload.skills])
    except BlueprintError as error:
        raise unprocessable(error) from error
    github = GitHubSkills(github_token_for(session, cipher, environment_id))
    choices = [(choice.path, choice.name) for choice in payload.skills]
    try:
        result = await anyio.to_thread.run_sync(
            skill_install.install_at_commit, github, layers[-1], payload.url, payload.ref, payload.commit, choices
        )
    except BlueprintError as error:
        raise unprocessable(error) from error
    return InstalledSkillsOut(
        skills=[InstalledSkillOut(name=name, path=f"skills/{name}/SKILL.md") for name in result.names],
        commits=[mutation_status(f"skills/{name}", commit) for name, commit in zip(result.names, result.commits)],
    )


@router.get("/skills/{name}/update", response_model=SkillUpdateOut)
async def check_skill_update(
    name: str,
    layers: LayersDependency,
    session: SessionDependency,
    cipher: CipherDependency,
    environment_id: Annotated[int | None, Query()] = None,
) -> SkillUpdateOut:
    """Compare an installed skill of this level with the newest commit of the ref it was installed from."""
    own = layers[-1]
    try:
        origin = operations.installed_skill_origin(own, name)
    except BlueprintNotFoundError as error:
        raise not_found(error) from error
    github = GitHubSkills(github_token_for(session, cipher, environment_id))
    try:
        check = await anyio.to_thread.run_sync(skill_install.check_update, github, own, name, origin)
    except BlueprintError as error:
        raise unprocessable(error) from error
    return SkillUpdateOut(
        up_to_date=check.latest.commit == origin.source.commit,
        installed_commit=origin.source.commit,
        latest_commit=check.latest.commit,
        ref=origin.source.ref,
        upstream_diff=check.upstream_diff,
        local_diff=check.local_diff,
        changed_locally=origin.changed_locally,
        candidate=candidate_out(check.candidate, own) if check.candidate else None,
        source=source_out(check.latest),
        url=str(origin.record.get("url") or f"https://github.com/{origin.source.repository}"),
    )


# --- Packages -----------------------------------------------------------------------------

# --- Packages -----------------------------------------------------------------------------


@router.post("/packages/preview", response_model=PackagePreviewOut)
async def preview_package(
    payload: GitHubPreviewIn,
    layers: LayersDependency,
    session: SessionDependency,
    cipher: CipherDependency,
    environment_id: Annotated[int | None, Query()] = None,
) -> PackagePreviewOut:
    """Resolve a package link to a fixed commit and describe its items for review; nothing is written."""
    github = GitHubSkills(github_token_for(session, cipher, environment_id))
    try:
        package = await anyio.to_thread.run_sync(packages.preview, github, payload.url)
    except BlueprintError as error:
        raise unprocessable(error) from error
    own = layers[-1]
    installed = packages.installed_items(own)
    return PackagePreviewOut(
        source=source_out(package.source),
        package=PackageInfoOut(name=package.manifest.name, description=package.manifest.description),
        items=[package_item_out(item, own, installed) for item in package.items],
    )


@router.post("/packages/install", response_model=PackageInstallOut)
async def install_package(
    payload: PackageInstallIn,
    layers: LayersDependency,
    session: SessionDependency,
    cipher: CipherDependency,
    environment_id: Annotated[int | None, Query()] = None,
) -> PackageInstallOut:
    """Install the chosen items at the reviewed commit into this level, as one commit; returns their paths."""
    github = GitHubSkills(github_token_for(session, cipher, environment_id))
    choices = [(choice.kind, choice.path.strip("/"), choice.name) for choice in payload.items]
    try:
        result = await anyio.to_thread.run_sync(
            packages.install_from_link, github, layers[-1], payload.url, payload.ref, payload.commit, choices
        )
    except BlueprintError as error:
        raise unprocessable(error) from error
    return PackageInstallOut(
        items=result.paths,
        commit=mutation_status(", ".join(result.paths), result.commit),
    )


@router.get("/packages", response_model=list[InstalledPackageOut])
def list_packages(layers: LayersDependency) -> list[InstalledPackageOut]:
    """The packages installed at this level, with their items."""
    own = layers[-1]
    installed = packages.installed_items(own)
    return [
        InstalledPackageOut(
            name=record["name"],
            description=str(record.get("description") or ""),
            url=record["url"],
            repository=record["repository"],
            ref=record["ref"],
            items=[
                PackageInstalledItemOut(
                    path=target,
                    kind=entry["kind"],
                    name=str(entry.get("name") or ""),
                    commit=str(entry.get("commit") or ""),
                    changed_locally=installed[target].changed_locally,
                )
                for target, entry in sorted(record["items"].items())
                if entry.get("kind") in packages.FOLDERS and target in installed
            ],
        )
        for record in packages.read_records(own)
        if record["items"]
    ]


@router.get("/packages/{name}/update", response_model=PackageUpdateOut)
async def check_package_update(
    name: str,
    layers: LayersDependency,
    session: SessionDependency,
    cipher: CipherDependency,
    environment_id: Annotated[int | None, Query()] = None,
    ref: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
) -> PackageUpdateOut:
    """Compare the package's items at this level with the newest commit of its ref, per item; nothing is written.

    `ref` reads another ref than the recorded one, such as the starter's tag of a newer YAAP version.
    """
    own = layers[-1]
    try:
        operations.require_installed_package(own, name)
    except BlueprintNotFoundError as error:
        raise not_found(error) from error
    github = GitHubSkills(github_token_for(session, cipher, environment_id))
    try:
        check = await anyio.to_thread.run_sync(package_updates.check_update, github, own, name, ref)
    except BlueprintError as error:
        raise unprocessable(error) from error
    installed = packages.installed_items(own)
    latest = check.latest.source
    return PackageUpdateOut(
        name=name,
        ref=latest.ref,
        latest_commit=latest.commit,
        source=source_out(latest),
        items=[
            PackageItemUpdateOut(
                path=update.target,
                kind=update.kind,  # type: ignore[arg-type]  # the service's kind is one of the schema's values
                name=update.name,
                installed_commit=update.installed_commit,
                status=update.status,  # type: ignore[arg-type]  # the service's status is one of the schema's values
                upstream_diff=update.upstream_diff,
                local_diff=update.local_diff,
                changed_locally=update.changed_locally,
                item=package_item_out(update.item, own, installed, replacing=False)
                if update.item and update.status == "changed"
                else None,
            )
            for update in check.items
        ],
        new_items=[package_item_out(item, own, installed) for item in check.new_items],
    )


@router.post("/packages/{name}/update", response_model=PackageInstallOut)
async def accept_package_update(
    name: str,
    payload: PackageAcceptIn,
    layers: LayersDependency,
    session: SessionDependency,
    cipher: CipherDependency,
    environment_id: Annotated[int | None, Query()] = None,
) -> PackageInstallOut:
    """Write the accepted items exactly as they are at the reviewed commit, as one commit; returns their paths."""
    own = layers[-1]
    try:
        operations.require_installed_package(own, name)
    except BlueprintNotFoundError as error:
        raise not_found(error) from error
    github = GitHubSkills(github_token_for(session, cipher, environment_id))
    try:
        result = await anyio.to_thread.run_sync(
            package_updates.accept_update, github, own, name, payload.commit, payload.paths, payload.ref
        )
    except BlueprintError as error:
        raise unprocessable(error) from error
    return PackageInstallOut(
        items=result.paths,
        commit=mutation_status(", ".join(result.paths), result.commit),
    )


# --- Remember as rule ------------------------------------------------------------------------------


@router.post("/rule", response_model=RuleOut)
def remember_rule(layers: LayersDependency, payload: RuleIn) -> RuleOut:
    """Add a rule to a file this level owns: preview the diff, then apply it as one commit."""
    try:
        change = operations.remember_rule(layers[-1], payload.rule, payload.path, payload.apply)
    except BlueprintError as error:
        raise unprocessable(error) from error
    return RuleOut(
        path=change.path,
        diff=change.diff,
        applied=payload.apply,
        mutation=mutation_status(change.path, change.mutation) if change.mutation is not None else None,
    )
