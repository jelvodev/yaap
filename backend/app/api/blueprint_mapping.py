"""Maps Blueprint domain values to the API response shapes."""

from app.api.blueprint_schemas import (
    BlueprintItemOut,
    GitHubSourceOut,
    MutationStatusOut,
    PackageFileOut,
    PackageItemOut,
    PackageOriginOut,
    SkillCandidateOut,
    SkillFileOut,
    SkillOriginOut,
)
from app.blueprint import packages, skill_install
from app.blueprint.layers import Item, Layer, layer_has_directory, layer_has_file
from app.blueprint.mutations import MutationResult
from app.blueprint.skill_install import read_origin


def mutation_status(path: str, result: MutationResult) -> MutationStatusOut:
    return MutationStatusOut(path=path, committed=result.committed, error=result.error)


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


def candidate_out(candidate: skill_install.Candidate, own: Layer) -> SkillCandidateOut:
    exists = layer_has_directory(own, f"skills/{candidate.name}")
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


def package_item_out(
    item: packages.PackageItem, own: Layer, installed: dict[str, packages.InstalledItem], replacing: bool = True
) -> PackageItemOut:
    exists = layer_has_directory(own, item.target) or layer_has_file(own, item.target)
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
