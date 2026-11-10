"""The Blueprint per level: list items with where they come from, read, write, switch off and reset.

A level is global (no environment), an environment, or a project in an environment.
"""

from typing import Annotated, Any, Literal

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, UploadFile, status
from pydantic import BaseModel, Field

from app.api.dependencies import CipherDependency, BlueprintDirDependency, SessionDependency, WorkspaceDirDependency
from app.blueprint import package_updates, packages, skill_install
from app.blueprint.layers import BlueprintError, Item, Layer, delete_file, merge_layers, validate_path, write_file
from app.blueprint.skill_install import GitHubSkills, read_origin
from app.core.utility import UtilityContext
from app.blueprint.importer import BlueprintImportError, collect_documents, item_path, propose, render, slug
from app.blueprint.levels import layers_for
from app.blueprint.rules import target_path, unified_diff, with_rule
from app.github.token import github_token_for
from app.storage.models import Environment, Project

router = APIRouter(prefix="/blueprint", tags=["blueprint"])

SWITCHED_OFF = "---\ndisabled: true\n---\n"


class SkillOriginOut(BaseModel):
    """Where an installed skill comes from."""

    url: str
    repository: str
    ref: str
    commit: str
    path: str
    # The skill's folder on GitHub at the installed commit.
    commit_url: str
    installed_at: str
    # The files differ from what was installed.
    changed_locally: bool


class PackageOriginOut(BaseModel):
    """The package an item was installed from."""

    name: str
    url: str
    repository: str
    ref: str
    commit: str
    # The item's file or folder on GitHub at the installed commit.
    commit_url: str
    installed_at: str
    # The files differ from what was installed.
    changed_locally: bool


class BlueprintItemOut(BaseModel):
    path: str
    kind: Literal["instructions", "standard", "engineering", "skill", "command", "agent"]
    name: str
    summary: str
    rules: list[str]
    disabled: bool
    # Where the effective file lives: global, environment or project, with that level's name.
    source: Literal["global", "environment", "project"]
    source_label: str
    # The level being viewed has its own file for this path.
    defined_here: bool
    # A level above also defines this path, so removing this level's file brings that one back.
    inherited_from: str | None
    origin: SkillOriginOut | None = None
    package: PackageOriginOut | None = None
    # Levels that add to this skill instead of replacing it, top first.
    extended_by: list[str] = Field(default_factory=list)
    # The level being viewed adds to the inherited skill with its own file.
    extended_here: bool = False
    # This level's file extends a skill that no level above has; it is not used.
    orphan_extension: bool = False


class BlueprintFileOut(BlueprintItemOut):
    # This level's own file when it has one (an override or an extension), else the effective file.
    content: str
    # For an extension: the skill as the levels above give it, shown read-only above the additions.
    inherited_content: str | None = None


class BlueprintFileWrite(BaseModel):
    content: str = Field(max_length=512 * 1024)


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


def origin_out(item: Item) -> SkillOriginOut | None:
    if item.kind != "skill":
        return None
    origin = read_origin(item.source, item.name)
    if origin is None or origin.record.get("package"):
        # A skill from a package shows and updates through its package.
        return None
    record = origin.record
    return SkillOriginOut(
        url=str(record.get("url") or ""),
        repository=record["repository"],
        ref=record["ref"],
        commit=record["commit"],
        path=record["path"],
        commit_url=origin.source.tree_url(record["path"]),
        installed_at=str(record.get("installed_at") or ""),
        changed_locally=origin.changed_locally,
    )


PackageIndex = dict[Layer, dict[str, packages.InstalledItem]]


def package_index(layers: list[Layer]) -> PackageIndex:
    """Every item that came from a package, per layer; read once per listing."""
    return {layer: packages.installed_items(layer) for layer in layers}


def package_out(item: Item, index: PackageIndex | None = None) -> PackageOriginOut | None:
    found = (index if index is not None else package_index([item.source])).get(item.source, {})
    installed = found.get(item.path)
    if installed is None:
        return None
    return PackageOriginOut(
        name=installed.record["name"],
        url=installed.record["url"],
        repository=installed.record["repository"],
        ref=installed.record["ref"],
        commit=installed.commit,
        commit_url=installed.commit_url(),
        installed_at=str(installed.entry.get("installed_at") or ""),
        changed_locally=installed.changed_locally,
    )


def item_to_out(item: Item, own_layer: Layer, index: PackageIndex | None = None) -> BlueprintItemOut:
    return BlueprintItemOut(
        origin=origin_out(item),
        package=package_out(item, index),
        path=item.path,
        kind=item.kind,
        name=item.name,
        summary=item.summary,
        rules=item.rules,
        disabled=item.document.disabled,
        source=item.source.kind,
        source_label=item.source.label,
        defined_here=item.source == own_layer,
        inherited_from=(item.overrides[-1].label if item.overrides else None)
        if item.source == own_layer
        else item.source.label,
        extended_by=[layer.label for layer, _ in item.extensions],
        extended_here=any(layer == own_layer for layer, _ in item.extensions)
        or (item.orphan_extension and item.source == own_layer),
        orphan_extension=item.orphan_extension,
    )


def find_item(layers: list[Layer], path: str) -> Item | None:
    return next((item for item in merge_layers(layers) if item.path == path), None)


def checked_path(path: str) -> str:
    try:
        return validate_path(path)
    except BlueprintError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error


@router.get("/items", response_model=list[BlueprintItemOut])
def list_items(layers: LayersDependency) -> list[BlueprintItemOut]:
    index = package_index(layers)
    return [item_to_out(item, layers[-1], index) for item in merge_layers(layers)]


@router.get("/file", response_model=BlueprintFileOut)
def read_file(layers: LayersDependency, path: Annotated[str, Query()]) -> BlueprintFileOut:
    item = find_item(layers, checked_path(path))
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No Blueprint file at this path.")
    own = layers[-1]
    if any(layer == own for layer, _ in item.extensions):
        content = (own.root / item.path).read_text(encoding="utf-8", errors="replace")
        inherited = item.without_layer(own).text()
        return BlueprintFileOut(**item_to_out(item, own).model_dump(), content=content, inherited_content=inherited)
    content = (item.source.root / item.path).read_text(encoding="utf-8", errors="replace")
    # An inherited skill this level could add to: the full text it would add to, additions of levels above included.
    inherited = item.text() if item.kind == "skill" and item.source != own and not item.orphan_extension else None
    return BlueprintFileOut(**item_to_out(item, own).model_dump(), content=content, inherited_content=inherited)


@router.put("/file", response_model=BlueprintFileOut)
def save_file(
    layers: LayersDependency, path: Annotated[str, Query()], payload: BlueprintFileWrite
) -> BlueprintFileOut:
    """Create the file at this level, or override an inherited one."""
    own = layers[-1]
    try:
        write_file(own, checked_path(path), payload.content, f"Update {path} ({own.label})")
    except BlueprintError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    return read_file(layers, path)


@router.post("/switch-off", response_model=BlueprintItemOut)
def switch_off(layers: LayersDependency, path: Annotated[str, Query()]) -> BlueprintItemOut:
    """Switch an inherited item off for this level and below, without touching the level that defines it."""
    own = layers[-1]
    item = find_item(layers, checked_path(path))
    if item is None or (item.source == own and not item.overrides):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Only an inherited item can be switched off. Delete an item of this level instead."
        )
    try:
        write_file(own, path, SWITCHED_OFF, f"Switch off {path} ({own.label})")
    except BlueprintError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    switched = find_item(layers, path)
    assert switched is not None
    return item_to_out(switched, own)


@router.delete("/file", status_code=status.HTTP_204_NO_CONTENT)
def remove_file(layers: LayersDependency, path: Annotated[str, Query()]) -> Response:
    """Remove this level's file: an override or switch-off is reset to inherited; an own item is deleted."""
    own = layers[-1]
    try:
        delete_file(own, checked_path(path), f"Remove {path} ({own.label})")
        packages.forget_items(own, [path], f"Forget {path} in its package ({own.label})")
    except BlueprintError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Import --------------------------------------------------------------------------------------------------------


class ImportProposalOut(BaseModel):
    source: str
    kind: Literal["instructions", "standard", "engineering", "skill", "command", "agent"]
    name: str
    summary: str
    rules: list[str]
    body: str
    # The body with each hidden character shown as a mark such as ⟦U+200B ZERO WIDTH SPACE⟧.
    body_marked: str
    warnings: list[str]
    # Front matter of the document that YAAP keeps as it is (for example preset or tools of an agent).
    extra: dict[str, Any]
    proposed_by_model: bool
    # The path the item gets, and whether this level already has a file there.
    path: str
    exists: bool


class ImportItemIn(BaseModel):
    kind: Literal["instructions", "standard", "engineering", "skill", "command", "agent"]
    name: str = Field(min_length=1, max_length=60)
    summary: str = Field(default="", max_length=1000)
    rules: list[str] = Field(default_factory=list, max_length=20)
    body: str = Field(min_length=1, max_length=200_000)
    extra: dict[str, Any] = Field(default_factory=dict)


class ImportApplyIn(BaseModel):
    items: list[ImportItemIn] = Field(min_length=1, max_length=60)


class ImportApplyOut(BaseModel):
    written: list[str]


@router.post("/import/analyze", response_model=list[ImportProposalOut])
async def analyze_import(
    request: Request,
    file: UploadFile,
    layers: LayersDependency,
    utility_environment_id: Annotated[int | None, Query()] = None,
) -> list[ImportProposalOut]:
    """Proposals for the documents in a Markdown file or zip; nothing is written yet."""
    try:
        documents = await anyio.to_thread.run_sync(collect_documents, file.file, file.filename or "upload.md")
    except BlueprintImportError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    state = request.app.state
    context = UtilityContext(state.session_factory, state.cipher, state.credential_types, state.providers)
    proposals = await propose(documents, context if utility_environment_id else None, utility_environment_id)
    own = layers[-1]
    return [
        ImportProposalOut(
            source=proposal.source,
            kind=proposal.kind,
            name=proposal.name,
            summary=proposal.summary,
            rules=proposal.rules,
            body=proposal.body,
            body_marked=proposal.body_marked,
            warnings=proposal.warnings,
            extra=proposal.extra,
            proposed_by_model=proposal.proposed_by_model,
            path=item_path(proposal.kind, proposal.name),
            exists=(own.root / item_path(proposal.kind, proposal.name)).exists(),
        )
        for proposal in proposals
    ]


@router.post("/import/apply", response_model=ImportApplyOut)
def apply_import(payload: ImportApplyIn, layers: LayersDependency) -> ImportApplyOut:
    """Write the reviewed items into this level; each file is committed on its own."""
    own = layers[-1]
    planned = [(item_path(item.kind, slug(item.name)), item) for item in payload.items]
    paths = [path for path, _ in planned]
    if len(set(paths)) != len(paths):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Two items would get the same path; rename one.")
    written: list[str] = []
    for path, item in planned:
        rules = [rule.strip() for rule in item.rules if rule.strip()]
        content = render(item.kind, item.summary.strip(), rules, item.body, item.extra)
        try:
            write_file(own, validate_path(path), content, f"Import {path} ({own.label})")
        except BlueprintError as error:
            done = ", ".join(written) or "none"
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, f"{path}: {error} Imported before it: {done}."
            ) from error
        written.append(path)
    return ImportApplyOut(written=written)


# --- Skills from GitHub --------------------------------------------------------------------


class GitHubSourceOut(BaseModel):
    repository: str
    ref: str
    commit: str
    path: str
    url: str


class SkillFileOut(BaseModel):
    path: str
    size: int


class SkillCandidateOut(BaseModel):
    # The skill's folder in the repository; what install refers to.
    path: str
    name: str
    description: str
    text: str
    # The text with each hidden character shown as a mark such as ⟦U+200B ZERO WIDTH SPACE⟧.
    text_marked: str
    files: list[SkillFileOut]
    size: int
    warnings: list[str]
    # Why it cannot be installed (too large, too many files, hidden characters), if so.
    error: str | None
    # The level being viewed already has a skill with this name; installing replaces it.
    exists: bool


class GitHubPreviewIn(BaseModel):
    url: str = Field(min_length=1, max_length=500)


class GitHubPreviewOut(BaseModel):
    source: GitHubSourceOut
    candidates: list[SkillCandidateOut]


class SkillChoiceIn(BaseModel):
    path: str = Field(max_length=500)
    name: str = Field(min_length=1, max_length=60)


class GitHubInstallIn(BaseModel):
    url: str = Field(min_length=1, max_length=500)
    ref: str = Field(min_length=1, max_length=200)
    # The commit that was reviewed; the install reads exactly this commit.
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    skills: list[SkillChoiceIn] = Field(min_length=1, max_length=30)


class InstalledSkillOut(BaseModel):
    name: str
    path: str


class SkillUpdateOut(BaseModel):
    up_to_date: bool
    installed_commit: str
    latest_commit: str
    ref: str
    # Upstream changes between the installed and the newest commit.
    upstream_diff: str
    # Changes made in YAAP since the install.
    local_diff: str
    changed_locally: bool
    candidate: SkillCandidateOut | None
    source: GitHubSourceOut
    url: str


def candidate_out(candidate: skill_install.Candidate, own: Layer) -> SkillCandidateOut:
    exists = (own.root / "skills" / candidate.name).exists()
    replaces = [f"Replaces the skill '{candidate.name}' at this level."] if exists else []
    return SkillCandidateOut(
        path=candidate.path,
        name=candidate.name,
        description=candidate.description,
        text=candidate.text,
        text_marked=candidate.text_marked,
        files=[SkillFileOut(path=file.path, size=file.size) for file in candidate.files],
        size=candidate.size,
        warnings=candidate.warnings + replaces,
        error=candidate.error,
        exists=exists,
    )


def source_out(source: skill_install.Source) -> GitHubSourceOut:
    return GitHubSourceOut(
        repository=source.repository,
        ref=source.ref,
        commit=source.commit,
        path=source.path,
        url=source.tree_url(source.path),
    )


def unprocessable(error: BlueprintError) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error))


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


@router.post("/skills/github/install", response_model=list[InstalledSkillOut])
async def install_github_skills(
    payload: GitHubInstallIn,
    layers: LayersDependency,
    session: SessionDependency,
    cipher: CipherDependency,
    environment_id: Annotated[int | None, Query()] = None,
) -> list[InstalledSkillOut]:
    """Install the reviewed skills at the reviewed commit into this level, one commit per skill."""
    names = [choice.name for choice in payload.skills]
    if len(set(names)) != len(names):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Two skills would get the same name; rename one.")
    github = GitHubSkills(github_token_for(session, cipher, environment_id))
    choices = [(choice.path, choice.name) for choice in payload.skills]
    try:
        written = await anyio.to_thread.run_sync(
            skill_install.install_at_commit, github, layers[-1], payload.url, payload.ref, payload.commit, choices
        )
    except BlueprintError as error:
        raise unprocessable(error) from error
    return [InstalledSkillOut(name=name, path=f"skills/{name}/SKILL.md") for name in written]


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
    origin = read_origin(own, name) if skill_install.SKILL_NAME.match(name) else None
    if origin is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This level has no skill with that name installed from GitHub.")
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


PackageKind = Literal["standard", "engineering", "skill", "command", "agent"]


class PackageFileOut(BaseModel):
    path: str
    size: int


class PackageItemOut(BaseModel):
    kind: PackageKind
    # The item's file or folder in the package; what install refers to.
    path: str
    name: str
    description: str
    text: str
    # The text with each hidden character shown as a mark such as ⟦U+200B ZERO WIDTH SPACE⟧.
    text_marked: str
    files: list[PackageFileOut]
    size: int
    warnings: list[str]
    # Why it cannot be installed (scripts, binary files, hidden characters, too large, missing), if so.
    error: str | None
    # The Blueprint path it gets, and whether the chosen level already has a file there.
    target: str
    exists: bool


class PackageInfoOut(BaseModel):
    name: str
    description: str


class PackagePreviewOut(BaseModel):
    source: GitHubSourceOut
    package: PackageInfoOut
    items: list[PackageItemOut]


class PackageChoiceIn(BaseModel):
    kind: PackageKind
    path: str = Field(min_length=1, max_length=500)
    name: str = Field(min_length=1, max_length=60)


class PackageInstallIn(BaseModel):
    url: str = Field(min_length=1, max_length=500)
    ref: str = Field(min_length=1, max_length=200)
    # The commit that was reviewed; the install reads exactly this commit.
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    items: list[PackageChoiceIn] = Field(min_length=1, max_length=packages.MAX_ITEMS)


class PackageInstalledItemOut(BaseModel):
    path: str
    kind: PackageKind
    name: str
    commit: str
    changed_locally: bool


class InstalledPackageOut(BaseModel):
    name: str
    description: str
    url: str
    repository: str
    ref: str
    items: list[PackageInstalledItemOut]


def package_item_out(
    item: packages.PackageItem, own: Layer, installed: dict[str, packages.InstalledItem], replacing: bool = True
) -> PackageItemOut:
    exists = (own.root / item.target).exists()
    warnings = list(item.warnings)
    if exists and replacing:
        owner = installed.get(item.target)
        source = f" from the package '{owner.record['name']}'" if owner else ""
        warnings.append(f"Replaces the {item.kind} '{item.name}'{source} at this level.")
    return PackageItemOut(
        kind=item.kind,  # type: ignore[arg-type]
        path=item.path,
        name=item.name,
        description=item.description,
        text=item.text,
        text_marked=item.text_marked,
        files=[PackageFileOut(path=file.path, size=file.size) for file in item.files],
        size=item.size,
        warnings=warnings,
        error=item.error,
        target=item.target,
        exists=exists,
    )


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


@router.post("/packages/install", response_model=list[str])
async def install_package(
    payload: PackageInstallIn,
    layers: LayersDependency,
    session: SessionDependency,
    cipher: CipherDependency,
    environment_id: Annotated[int | None, Query()] = None,
) -> list[str]:
    """Install the chosen items at the reviewed commit into this level, as one commit; returns their paths."""
    github = GitHubSkills(github_token_for(session, cipher, environment_id))
    choices = [(choice.kind, choice.path.strip("/"), choice.name) for choice in payload.items]
    try:
        return await anyio.to_thread.run_sync(
            packages.install_from_link, github, layers[-1], payload.url, payload.ref, payload.commit, choices
        )
    except BlueprintError as error:
        raise unprocessable(error) from error


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


class PackageItemUpdateOut(BaseModel):
    # The item's Blueprint path at this level; what accepting refers to.
    path: str
    kind: PackageKind
    name: str
    installed_commit: str
    # changed: an update to review and accept; removed: the newest version no longer lists it.
    status: Literal["up_to_date", "changed", "removed"]
    upstream_diff: str
    # Changes made in YAAP since the install.
    local_diff: str
    changed_locally: bool
    # The newest version for review, when it changed.
    item: PackageItemOut | None


class PackageUpdateOut(BaseModel):
    name: str
    ref: str
    latest_commit: str
    source: GitHubSourceOut
    items: list[PackageItemUpdateOut]
    # Items the newest version lists that this level has not installed; add them with "Add from a package".
    new_items: list[PackageItemOut]


class PackageAcceptIn(BaseModel):
    # The commit that was reviewed; accepting writes exactly this commit.
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    paths: list[str] = Field(min_length=1, max_length=packages.MAX_ITEMS)
    # The ref the reviewed commit was read from, when the check named one (the starter moving to a newer tag); it is
    # recorded instead of the old one. Without it the recorded ref stays.
    ref: str | None = Field(default=None, min_length=1, max_length=200)


def installed_package(own: Layer, name: str) -> None:
    if not skill_install.SKILL_NAME.match(name) or packages.read_record(own, name) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This level has no package with that name.")


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
    installed_package(own, name)
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
                kind=update.kind,  # type: ignore[arg-type]
                name=update.name,
                installed_commit=update.installed_commit,
                status=update.status,  # type: ignore[arg-type]
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


@router.post("/packages/{name}/update", response_model=list[str])
async def accept_package_update(
    name: str,
    payload: PackageAcceptIn,
    layers: LayersDependency,
    session: SessionDependency,
    cipher: CipherDependency,
    environment_id: Annotated[int | None, Query()] = None,
) -> list[str]:
    """Write the accepted items exactly as they are at the reviewed commit, as one commit; returns their paths."""
    own = layers[-1]
    installed_package(own, name)
    github = GitHubSkills(github_token_for(session, cipher, environment_id))
    try:
        return await anyio.to_thread.run_sync(
            package_updates.accept_update, github, own, name, payload.commit, payload.paths, payload.ref
        )
    except BlueprintError as error:
        raise unprocessable(error) from error


# --- Remember as rule ------------------------------------------------------------------------------


class RuleIn(BaseModel):
    rule: str = Field(min_length=1, max_length=500)
    # A standard, engineering standard or instructions.md this level owns; otherwise the level's rules standard.
    path: str | None = None
    # False only previews the change.
    apply: bool = False


class RuleOut(BaseModel):
    path: str
    diff: str
    applied: bool


@router.post("/rule", response_model=RuleOut)
def remember_rule(layers: LayersDependency, payload: RuleIn) -> RuleOut:
    """Add a rule to a file this level owns: preview the diff, then apply it as one commit."""
    own = layers[-1]
    try:
        path = target_path(own, payload.path)
        before, after = with_rule(own, path, payload.rule)
        if payload.apply:
            write_file(own, path, after, f"Remember rule: {' '.join(payload.rule.split())[:60]} ({own.label})")
    except BlueprintError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    return RuleOut(path=path, diff=unified_diff(path, before, after), applied=payload.apply)
