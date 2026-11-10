"""Updates of installed Blueprint packages.

An update reads the package at the newest commit of its ref (or of another ref the caller names, as the YAAP starter
does when YAAP moved to a new version), shows each installed item as a diff against the commit it was installed from,
and writes only the items the user accepts, through the same install as a first installation.
"""

from dataclasses import dataclass, replace
from typing import Any

from app.blueprint.layers import Layer
from app.blueprint.packages import (
    FOLDERS,
    Package,
    PackageError,
    PackageItem,
    download,
    install,
    item_files,
    read_package,
    read_record,
)
from app.blueprint.skill_install import GitHubLink, GitHubSkills, Source, files_hash, text_diff
from app.core.hidden_text import mark_hidden



@dataclass
class ItemUpdate:
    target: str
    kind: str
    name: str
    installed_commit: str
    # up_to_date: same content upstream; changed: an update to accept; removed: no longer in the package.
    status: str
    upstream_diff: str = ""
    local_diff: str = ""
    changed_locally: bool = False
    item: PackageItem | None = None


@dataclass
class PackageUpdate:
    record: dict[str, Any]
    latest: Package
    items: list[ItemUpdate]
    # Items the newest version lists that this level has not installed.
    new_items: list[PackageItem]


def check_update(github: GitHubSkills, layer: Layer, name: str, ref: str | None = None) -> PackageUpdate:
    """Compare every installed item of a package with the newest commit of its ref; nothing is written.

    `ref` reads another ref than the recorded one, for a package pinned to a tag that a newer tag replaces.
    """
    record = read_record(layer, name)
    if record is None:
        raise PackageError(f"This level has no package '{name}'.")
    owner, repo = record["repository"].split("/", 1)
    repository = GitHubLink(owner, repo)
    latest_source, latest_tree = github.resolve(repository, ref or record["ref"], record["path"])
    latest_source.ref = ref or record["ref"]
    latest = read_package(github, latest_source, latest_tree)
    # The package at each installed commit; None when that commit can no longer be read (for example after a force
    # push), so the diff then shows the newest version in full.
    older: dict[str, Package | None] = {latest_source.commit: latest}
    updates: list[ItemUpdate] = []
    for target, entry in sorted(record["items"].items()):
        kind, source_path, installed_name = entry.get("kind"), entry.get("source"), entry.get("name")
        commit = str(entry.get("commit") or "")
        if kind not in FOLDERS or not isinstance(source_path, str) or not isinstance(installed_name, str):
            continue
        local = item_files(layer, target)
        update = ItemUpdate(target, kind, installed_name, commit, "up_to_date")
        update.changed_locally = files_hash(local) != entry.get("hash")
        moved = commit != latest_source.commit
        old_files: dict[str, bytes] = {}
        if moved or update.changed_locally:
            if commit not in older:
                found = github.commit_of(record["repository"], commit)
                if found is None:
                    older[commit] = None
                else:
                    old_source = Source(record["repository"], record["ref"], found[0], record["path"])
                    older[commit] = read_package(github, old_source, found[1])
            old_package = older[commit]
            old_item = old_package.item(kind, source_path) if old_package else None
            if old_package is not None and old_item is not None and not old_item.error:
                old_files = download(github, old_package.source, replace(old_item, name=installed_name))
        new_item = latest.item(kind, source_path)
        if new_item is None:
            update.status = "removed"
        elif moved:
            new_item = replace(new_item, name=installed_name)
            update.item = new_item
            try:
                new_files = {} if new_item.error else download(github, latest_source, new_item)
            except PackageError as error:  # A file the listing did not show is refused (for example hidden characters).
                new_item = replace(new_item, error=str(error))
                update.item = new_item
                new_files = {}
            if new_item.error:
                update.status = "changed"
            elif new_files != old_files:
                update.status = "changed"
                diff = text_diff(old_files, new_files, commit[:7], latest_source.commit[:7])
                update.upstream_diff = mark_hidden(diff)
        if update.changed_locally:
            update.local_diff = text_diff(old_files, local, commit[:7], "local")
        updates.append(update)
    # New: listed now, but not at any commit the level installed from (every such commit was read above).
    earlier = [package for commit, package in older.items() if commit != latest_source.commit and package]
    taken = set(record["items"])
    new_items = [
        item for item in latest.items
        if earlier and item.target not in taken and not any(package.item(item.kind, item.path) for package in earlier)
    ]
    return PackageUpdate(record, latest, updates, new_items)


def accept_update(
    github: GitHubSkills, layer: Layer, name: str, commit: str, targets: list[str], ref: str | None = None
) -> list[str]:
    """Write the accepted items exactly as they are at the reviewed commit; `ref` is recorded instead of the old one."""
    record = read_record(layer, name)
    if record is None:
        raise PackageError(f"This level has no package '{name}'.")
    choices: list[tuple[str, str, str]] = []
    for target in targets:
        entry = record["items"].get(target)
        if entry is None:
            raise PackageError(f"'{target}' is not an installed item of the package '{name}'.")
        choices.append((str(entry["kind"]), str(entry["source"]), str(entry["name"])))
    source = Source(record["repository"], ref or record["ref"], commit, record["path"])
    return install(github, layer, record["url"], source, choices, action="Update")
