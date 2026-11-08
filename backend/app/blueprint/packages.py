"""Blueprint packages from GitHub.

A package is a GitHub repository (or a folder in one) with a `blueprint-package.json` that lists Blueprint items:
standards, engineering standards, skills, commands and agents (`docs/blueprint-packages.md`). It is read like a skill
from GitHub: the link is resolved to a fixed commit, only the package folder is read, and every item is shown for
review before anything is written. A package is text only: an item with scripts, executables or binary files is
refused, and nothing that is downloaded is ever run.

Installing writes the chosen items into one layer, unchanged, and records where each one came from in
`packages/<name>.json` of that layer (the commit per item and a hash of its files). A skill also gets the usual
`source.json`, so it counts as outside content for the injection guard like any skill from GitHub. An update is
read the same way at the newest commit of the package's ref, shown as a diff per item, and written only for the items
the user accepts.
"""

import json
import shutil
import urllib.parse
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from app.blueprint.layers import (
    MAX_FILE_BYTES,
    BlueprintError,
    Layer,
    ensure_repository,
    forget_merged,
    parse_document,
    validate_path,
)
from app.blueprint.skill_install import (
    BINARY_SUFFIXES,
    LINK,
    MAX_SKILL_BYTES,
    MAX_SKILL_FILES,
    RUN_TEXT,
    SCRIPT_SUFFIXES,
    SKILL_NAME,
    SOURCE_FILE,
    GitHubLink,
    GitHubSkills,
    RemoteFile,
    SkillInstallError,
    Source,
    files_hash,
    local_files,
    safe_relative,
    skill_name,
    text_diff,
)
from app.git.service import GitError, run_git

MANIFEST = "blueprint-package.json"
MAX_MANIFEST_BYTES = 64 * 1024
MAX_ITEMS = 60
RECORDS_FOLDER = "packages"
# Item kinds a package can hold, with the layer folder each one is written to. instructions.md stays personal.
FOLDERS = {"standard": "standards", "engineering": "engineering", "skill": "skills", "command": "commands", "agent": "agents"}
EXECUTABLE_MODE = "100755"
# Key of a record as read: the items whose files are gone. It is never written.
STALE = "stale"


class PackageError(SkillInstallError):
    """A package action that cannot be done; the message is safe to show to the user."""


# --- The link and the manifest -------------------------------------------------------------------------------------


def parse_package_link(url: str) -> GitHubLink:
    match = LINK.match(url.strip().split("#", 1)[0].split("?", 1)[0])
    if not match:
        raise PackageError(
            "Use a GitHub link: https://github.com/<owner>/<repo>, a folder (…/tree/<ref>/<path>) "
            f"or a {MANIFEST} file (…/blob/<ref>/<path>/{MANIFEST})."
        )
    rest = urllib.parse.unquote(match.group("rest")) if match.group("rest") else None
    is_file = match.group("kind") == "blob"
    if is_file and not (rest or "").endswith(MANIFEST):
        raise PackageError(f"A file link must point to a {MANIFEST}.")
    return GitHubLink(match.group("owner"), match.group("repo"), rest, is_file)


def package_folder(link: GitHubLink, ref: str) -> str:
    """The package folder a link names, once its ref is known (the ref and path are not separated in a link)."""
    if link.rest is None:
        return ""
    if link.rest != ref and not link.rest.startswith(f"{ref}/"):
        raise PackageError(f"The link does not name the ref '{ref}'.")
    rest = link.rest[len(ref) + 1 :]
    folder = str(PurePosixPath(rest).parent) if link.is_file else rest
    return "" if folder == "." else folder.strip("/")


@dataclass
class ManifestItem:
    kind: str
    # The item's file (standards, commands, agents) or folder (skills), relative to the package folder.
    path: str
    name: str
    description: str


@dataclass
class Manifest:
    name: str
    description: str
    items: list[ManifestItem]


def target_path(kind: str, name: str) -> str:
    """The Blueprint path an item gets in a layer."""
    folder = FOLDERS[kind]
    return f"skills/{name}/SKILL.md" if kind == "skill" else f"{folder}/{name}.md"


def parse_manifest(data: bytes) -> Manifest:
    try:
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise PackageError(f"{MANIFEST} is not valid JSON: {error}") from error
    if not isinstance(raw, dict):
        raise PackageError(f"{MANIFEST} must be a JSON object with name, description and items.")
    name = raw.get("name")
    if not isinstance(name, str) or not SKILL_NAME.match(name):
        raise PackageError(f"{MANIFEST}: name uses lowercase letters, digits and hyphens (at most 60).")
    description = raw.get("description")
    if not isinstance(description, str):
        raise PackageError(f"{MANIFEST}: description is a text.")
    entries = raw.get("items")
    if not isinstance(entries, list) or not entries:
        raise PackageError(f"{MANIFEST}: items is a list with at least one item.")
    if len(entries) > MAX_ITEMS:
        raise PackageError(f"{MANIFEST}: a package holds at most {MAX_ITEMS} items.")
    items: list[ManifestItem] = []
    for number, entry in enumerate(entries, start=1):
        where = f"{MANIFEST}: item {number}"
        if not isinstance(entry, dict):
            raise PackageError(f"{where} must be an object with kind and path.")
        kind = entry.get("kind")
        if kind not in FOLDERS:
            raise PackageError(f"{where}: kind is one of {', '.join(FOLDERS)}.")
        path = entry.get("path")
        if not isinstance(path, str) or not path.strip("/"):
            raise PackageError(f"{where}: path is the item's file or folder in the package.")
        path = path.strip("/")
        try:
            safe_relative(path)
        except SkillInstallError as error:
            raise PackageError(f"{where}: the path '{path}' leaves the package folder.") from error
        if kind != "skill" and not path.endswith(".md"):
            raise PackageError(f"{where}: a {kind} is one Markdown file (.md).")
        if kind == "skill" and PurePosixPath(path).name == "SKILL.md":
            path = str(PurePosixPath(path).parent).strip(".") or ""
            if not path:
                raise PackageError(f"{where}: a skill is a folder with a SKILL.md, not the package folder itself.")
        given = entry.get("name")
        if given is not None and (not isinstance(given, str) or not SKILL_NAME.match(given)):
            raise PackageError(f"{where}: name uses lowercase letters, digits and hyphens (at most 60).")
        default = PurePosixPath(path).name if kind == "skill" else PurePosixPath(path).stem
        item_description = entry.get("description", "")
        if not isinstance(item_description, str):
            raise PackageError(f"{where}: description is a text.")
        items.append(ManifestItem(kind, path, given or skill_name(default), item_description.strip()))
    targets = [target_path(item.kind, item.name) for item in items]
    duplicate = next((target for index, target in enumerate(targets) if target in targets[:index]), None)
    if duplicate:
        raise PackageError(f"{MANIFEST}: two items would both be written to {duplicate}.")
    return Manifest(name, description.strip(), items)


# --- Reading a package at one commit ---------------------------------------------------------------------------------


@dataclass
class PackageItem:
    kind: str
    path: str
    name: str
    description: str
    text: str = ""
    files: list[RemoteFile] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Why it cannot be installed (scripts, binary files, too large, missing), if so.
    error: str | None = None

    @property
    def size(self) -> int:
        return sum(file.size for file in self.files)

    @property
    def target(self) -> str:
        return target_path(self.kind, self.name)


@dataclass
class Package:
    source: Source
    manifest: Manifest
    items: list[PackageItem]

    def item(self, kind: str, path: str) -> PackageItem | None:
        return next((item for item in self.items if item.kind == kind and item.path == path), None)


def code_files(files: list[RemoteFile]) -> tuple[list[str], list[str]]:
    """Scripts (by suffix or executable bit) and binary files (by suffix) among the files."""
    scripts = [
        file.path for file in files
        if PurePosixPath(file.path).suffix.lower() in SCRIPT_SUFFIXES or file.mode == EXECUTABLE_MODE
    ]
    binaries = [file.path for file in files if PurePosixPath(file.path).suffix.lower() in BINARY_SUFFIXES]
    return scripts, binaries


def text_only_error(scripts: list[str], binaries: list[str]) -> str | None:
    found = [*(f"script {path}" for path in scripts), *(f"binary file {path}" for path in binaries)]
    if not found:
        return None
    shown = ", ".join(found[:5]) + (f" and {len(found) - 5} more" if len(found) > 5 else "")
    return f"A package holds text only; YAAP refuses this item because it contains {shown}."


def read_package(github: GitHubSkills, source: Source, root_tree: str) -> Package:
    """The manifest and every item it lists at the source's commit, described for review."""
    entries = github.folder_tree(source, root_tree)
    manifest_entry = next((entry for entry in entries if entry["path"] == MANIFEST), None)
    if manifest_entry is None or manifest_entry["type"] != "blob" or manifest_entry["mode"] == "120000":
        where = f"'{source.path}'" if source.path else "the repository root"
        raise PackageError(f"No {MANIFEST} was found in {where} of {source.repository}.")
    if int(manifest_entry.get("size") or 0) > MAX_MANIFEST_BYTES:
        raise PackageError(f"{MANIFEST} is larger than 64 KB.")
    manifest = parse_manifest(github.raw(source, MANIFEST))
    items = [describe(github, source, entries, entry) for entry in manifest.items]
    return Package(source, manifest, items)


def describe(github: GitHubSkills, source: Source, entries: list[dict[str, Any]], entry: ManifestItem) -> PackageItem:
    item = PackageItem(entry.kind, entry.path, entry.name, entry.description)
    if entry.kind == "skill":
        prefix = f"{entry.path}/"
        for found in entries:
            if not found["path"].startswith(prefix) or found["type"] == "tree":
                continue
            relative = found["path"][len(prefix) :]
            if found["type"] != "blob" or found["mode"] == "120000" or relative == SOURCE_FILE:
                item.skipped.append(relative)  # Symlinks and submodules are never followed; YAAP writes source.json.
            else:
                item.files.append(RemoteFile(relative, int(found.get("size") or 0), found["mode"]))
        main = "SKILL.md"
        if not any(file.path == main for file in item.files):
            item.error = f"The package has no skill folder '{entry.path}' with a SKILL.md."
            return item
        if len(item.files) > MAX_SKILL_FILES:
            item.error = f"The skill has {len(item.files)} files; at most {MAX_SKILL_FILES} can be installed."
        elif item.size > MAX_SKILL_BYTES:
            item.error = "The skill is larger than 5 MB."
        text_path = f"{entry.path}/{main}"
    else:
        found = next((other for other in entries if other["path"] == entry.path), None)
        if found is None or found["type"] != "blob":
            item.error = f"The package has no file '{entry.path}'."
            return item
        if found["mode"] == "120000":
            item.error = f"'{entry.path}' is a symlink; YAAP does not follow links."
            return item
        item.files.append(RemoteFile(PurePosixPath(entry.path).name, int(found.get("size") or 0), found["mode"]))
        if item.size > MAX_FILE_BYTES:
            item.error = "The file is larger than 512 KB."
            return item
        text_path = entry.path
    item.error = item.error or text_only_error(*code_files(item.files))
    content = github.raw(source, text_path)
    if b"\0" in content:
        item.error = item.error or text_only_error([], [text_path])
        return item
    item.text = content.decode("utf-8", errors="replace").replace("\r\n", "\n")
    try:
        front = parse_document(item.text).front_matter
    except BlueprintError as error:
        front = {}
        if entry.kind == "skill":
            item.warnings.append("The front matter of SKILL.md is not valid YAML.")
        else:
            item.error = item.error or str(error)
    item.description = item.description or str(front.get("summary") or front.get("description") or "").strip()
    if item.skipped:
        item.warnings.append(f"Skipped: {', '.join(item.skipped[:5])} (symlinks, submodules or source.json).")
    match = RUN_TEXT.search(item.text)
    if match:
        item.warnings.append(f"The text tells the agent to download or install things: \"{match.group(0)[:80]}\".")
    return item


def download(github: GitHubSkills, source: Source, item: PackageItem) -> dict[str, bytes]:
    """The item's files by their path in the layer, checked to be text."""
    if item.error:
        raise PackageError(f"{item.name}: {item.error}")
    folder = Source(source.repository, source.ref, source.commit, "/".join(p for p in (source.path, item.path) if p))
    files: dict[str, bytes] = {}
    total = 0
    if item.kind == "skill":
        for file in item.files:
            content = github.raw(folder, file.path)
            total += len(content)
            if total > MAX_SKILL_BYTES:
                raise PackageError(f"{item.name}: the skill is larger than 5 MB.")
            check_text(item, file.path, content)
            files[f"skills/{item.name}/{safe_relative(file.path)}"] = content
        return files
    content = github.raw(source, item.path)
    if len(content) > MAX_FILE_BYTES:
        raise PackageError(f"{item.name}: the file is larger than 512 KB.")
    check_text(item, item.path, content)
    text = content.decode("utf-8").replace("\r\n", "\n")
    try:
        parse_document(text)
    except BlueprintError as error:
        raise PackageError(f"{item.name}: {error}") from error
    return {target_path(item.kind, item.name): text.encode("utf-8")}


def check_text(item: PackageItem, path: str, content: bytes) -> None:
    """What the listing could not show: binary content, a script without a script suffix, or text that is not UTF-8."""
    if b"\0" in content:
        raise PackageError(f"{item.name}: {text_only_error([], [path])}")
    if content.startswith(b"#!"):
        raise PackageError(f"{item.name}: {text_only_error([path], [])}")
    try:
        content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PackageError(f"{item.name}: {path} is not UTF-8 text.") from error


def preview(github: GitHubSkills, url: str) -> Package:
    """The fixed commit a link resolves to and the package found there, described for review."""
    source, tree = github.resolve(parse_package_link(url))
    return read_package(github, source, tree)


# --- Records of installed packages in a layer ------------------------------------------------------------------------


def record_file(layer: Layer, name: str) -> Path:
    if not SKILL_NAME.match(name):
        raise PackageError("A package name uses lowercase letters, digits and hyphens (at most 60).")
    return layer.root / RECORDS_FOLDER / f"{name}.json"


def item_files(layer: Layer, target: str) -> dict[str, bytes]:
    """An installed item's files in the layer, by their path in the layer (without a skill's source.json)."""
    if target.startswith("skills/"):
        folder = layer.root / PurePosixPath(target).parent
        if not folder.is_dir():
            return {}
        prefix = PurePosixPath(target).parent.as_posix()
        return {f"{prefix}/{path}": content for path, content in local_files(folder).items()}
    file = layer.root / target
    return {target: file.read_bytes()} if file.is_file() else {}


def read_record(layer: Layer, name: str) -> dict[str, Any] | None:
    """A package's record, with only the items whose files still exist."""
    file = record_file(layer, name)
    if not file.is_file():
        return None
    try:
        record = json.loads(file.read_text(encoding="utf-8"))
        if not all(isinstance(record.get(key), str) for key in ("name", "repository", "ref", "path", "url")):
            return None
        items = record.get("items") if isinstance(record.get("items"), dict) else {}
        record["items"] = {
            target: entry
            for target, entry in items.items()
            if isinstance(entry, dict) and (layer.root / target).is_file()
        }
        # Items whose files are gone (deleted outside the Blueprint screen); dropped from the file on the next write.
        record[STALE] = [target for target in items if target not in record["items"]]
    except (OSError, ValueError, AttributeError):
        return None
    return record


def read_records(layer: Layer) -> list[dict[str, Any]]:
    folder = layer.root / RECORDS_FOLDER
    if not folder.is_dir():
        return []
    records = [read_record(layer, file.stem) for file in sorted(folder.glob("*.json")) if SKILL_NAME.match(file.stem)]
    return [record for record in records if record is not None]


@dataclass
class InstalledItem:
    record: dict[str, Any]
    target: str
    entry: dict[str, Any]
    changed_locally: bool

    @property
    def commit(self) -> str:
        return str(self.entry.get("commit") or "")

    def commit_url(self) -> str:
        source_path = "/".join(part for part in (self.record["path"], str(self.entry.get("source") or "")) if part)
        kind = "tree" if self.target.startswith("skills/") else "blob"
        return f"https://github.com/{self.record['repository']}/{kind}/{self.commit}/{source_path}"


def installed_items(layer: Layer) -> dict[str, InstalledItem]:
    """Every item of this layer that came from a package, by its Blueprint path."""
    found: dict[str, InstalledItem] = {}
    for record in read_records(layer):
        for target, entry in record["items"].items():
            changed = files_hash(item_files(layer, target)) != entry.get("hash")
            found[target] = InstalledItem(record, target, entry, changed)
    return found


def write_record(layer: Layer, record: dict[str, Any]) -> str:
    file = record_file(layer, record["name"])
    file.parent.mkdir(parents=True, exist_ok=True)
    stored = {key: value for key, value in record.items() if key != STALE}
    file.write_text(json.dumps(stored, indent=2) + "\n", encoding="utf-8")
    return f"{RECORDS_FOLDER}/{file.name}"


def commit_paths(layer: Layer, paths: list[str], message: str) -> None:
    pathspecs = [f"{layer.prefix}{path}" for path in paths]
    forget_merged()
    try:
        run_git(["add", "--all", "--", *pathspecs], layer.repository)
        if run_git(["status", "--porcelain", "--", *pathspecs], layer.repository).stdout.strip():
            run_git(["commit", "-m", message, "--", *pathspecs], layer.repository)
    except GitError as error:
        raise PackageError(f"The items were written, but committing them failed: {error}") from error


def forget_items(layer: Layer, targets: list[str], message: str) -> None:
    """Drop items from the records of their packages, for example after the item was deleted."""
    changed: list[str] = []
    for record in read_records(layer):
        kept = {target: entry for target, entry in record["items"].items() if target not in targets}
        if len(kept) != len(record["items"]) or any(target in record[STALE] for target in targets):
            record["items"] = kept
            changed.append(write_record(layer, record))
    if changed:
        commit_paths(layer, changed, message)


# --- Installing and updating -----------------------------------------------------------------------------------------


def install(
    github: GitHubSkills,
    layer: Layer,
    url: str,
    source: Source,
    choices: list[tuple[str, str, str]],
    action: str = "Install",
) -> list[str]:
    """Install items (kind, path in the package, name in the layer) exactly as they are at the source's commit.

    Everything is read and checked before anything is written; the items and the package record are one commit.
    """
    owner, repo = source.repository.split("/", 1)
    pinned, tree = github.resolve(GitHubLink(owner, repo), source.commit, source.path)
    pinned.ref = source.ref
    package = read_package(github, pinned, tree)
    name = package.manifest.name
    existing = read_record(layer, name)
    if existing is not None and existing["repository"] != pinned.repository:
        raise PackageError(f"This level already has a package '{name}' from {existing['repository']}.")
    planned: list[tuple[PackageItem, dict[str, bytes]]] = []
    for kind, path, item_name in choices:
        if not SKILL_NAME.match(item_name):
            raise PackageError(f"{item_name or 'An empty name'}: a name uses lowercase letters, digits and hyphens.")
        item = package.item(kind, path)
        if item is None:
            raise PackageError(f"The package has no {kind} at '{path}' at {pinned.commit[:7]}.")
        item = replace(item, name=item_name)
        validate_path(item.target)
        planned.append((item, download(github, pinned, item)))
    targets = [item.target for item, _ in planned]
    if len(set(targets)) != len(targets):
        raise PackageError("Two items would get the same path; rename one.")

    ensure_repository(layer)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    record = existing or {"name": name, "items": {}}
    record.update(
        name=name,
        description=package.manifest.description,
        url=url,
        repository=pinned.repository,
        ref=pinned.ref,
        path=pinned.path,
    )
    paths: list[str] = []
    for item, files in planned:
        target = item.target
        if item.kind == "skill":
            folder = layer.root / PurePosixPath(target).parent
            if folder.exists():
                shutil.rmtree(folder)
            prefix = f"{PurePosixPath(target).parent.as_posix()}/"
            relative = {path[len(prefix) :]: content for path, content in files.items()}
            source_record = {
                "url": url,
                "repository": pinned.repository,
                "ref": pinned.ref,
                "commit": pinned.commit,
                "path": "/".join(part for part in (pinned.path, item.path) if part),
                "installed_at": now,
                "hash": files_hash(relative),
                "package": name,
            }
            folder.mkdir(parents=True, exist_ok=True)
            (folder / SOURCE_FILE).write_text(json.dumps(source_record, indent=2) + "\n", encoding="utf-8")
            paths.append(prefix.rstrip("/"))
        else:
            paths.append(target)
        for path, content in files.items():
            file = layer.root / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(content)
        record["items"][target] = {
            "kind": item.kind,
            "name": item.name,
            "source": item.path,
            "commit": pinned.commit,
            "installed_at": now,
            "hash": files_hash(files),
        }
    # An item now belongs to this package only.
    for other in read_records(layer):
        if other["name"] != name and any(target in other["items"] for target in targets):
            other["items"] = {key: value for key, value in other["items"].items() if key not in targets}
            paths.append(write_record(layer, other))
    paths.append(write_record(layer, record))
    names = ", ".join(item.name for item, _ in planned)
    if len(names) > 80:
        names = f"{len(planned)} items"
    commit_paths(layer, paths, f"{action} {names} from package {name} ({pinned.repository}@{pinned.commit[:7]}, {layer.label})")
    return targets


def install_from_link(
    github: GitHubSkills, layer: Layer, url: str, ref: str, commit: str, choices: list[tuple[str, str, str]]
) -> list[str]:
    """Install reviewed items at the reviewed commit; the link and ref are those of the review."""
    link = parse_package_link(url)
    source = Source(link.repository, ref, commit, package_folder(link, ref))
    return install(github, layer, url, source, choices)


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


def check_update(github: GitHubSkills, layer: Layer, name: str) -> PackageUpdate:
    """Compare every installed item of a package with the newest commit of its ref; nothing is written."""
    record = read_record(layer, name)
    if record is None:
        raise PackageError(f"This level has no package '{name}'.")
    owner, repo = record["repository"].split("/", 1)
    repository = GitHubLink(owner, repo)
    latest_source, latest_tree = github.resolve(repository, record["ref"], record["path"])
    latest_source.ref = record["ref"]
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
            if new_item.error:
                update.status = "changed"
            else:
                new_files = download(github, latest_source, new_item)
                if new_files != old_files:
                    update.status = "changed"
                    update.upstream_diff = text_diff(old_files, new_files, commit[:7], latest_source.commit[:7])
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


def accept_update(github: GitHubSkills, layer: Layer, name: str, commit: str, targets: list[str]) -> list[str]:
    """Write the accepted items exactly as they are at the reviewed commit."""
    record = read_record(layer, name)
    if record is None:
        raise PackageError(f"This level has no package '{name}'.")
    choices: list[tuple[str, str, str]] = []
    for target in targets:
        entry = record["items"].get(target)
        if entry is None:
            raise PackageError(f"'{target}' is not an installed item of the package '{name}'.")
        choices.append((str(entry["kind"]), str(entry["source"]), str(entry["name"])))
    source = Source(record["repository"], record["ref"], commit, record["path"])
    return install(github, layer, record["url"], source, choices, action="Update")
