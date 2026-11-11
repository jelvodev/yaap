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
    layer_has_directory,
    layer_has_file,
    layer_tree_files,
    parse_document,
    read_layer_bytes,
    validate_path,
)
from app.blueprint.mutations import MutationResult, commit_paths, replace_folder_contents, write_bytes
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
    safe_relative,
    skill_name,
)
from app.core.hidden_text import find_hidden, mark_hidden, refusal_reason, warning_lines

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


@dataclass(frozen=True)
class PackageInstallResult:
    paths: list[str]
    commit: MutationResult


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
    # The text with hidden characters made visible, for review.
    text_marked: str = ""
    files: list[RemoteFile] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Why it cannot be installed (scripts, binary files, hidden characters, too large, missing), if so.
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
    findings = find_hidden(item.text)
    item.warnings += warning_lines(findings)
    item.text_marked = mark_hidden(item.text)
    item.error = item.error or refusal_reason(findings)
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
    """What the listing could not show: binary content, a script without a script suffix, text that is not UTF-8,
    or tag characters and direction controls hidden in the text."""
    if b"\0" in content:
        raise PackageError(f"{item.name}: {text_only_error([], [path])}")
    if content.startswith(b"#!"):
        raise PackageError(f"{item.name}: {text_only_error([path], [])}")
    try:
        content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PackageError(f"{item.name}: {path} is not UTF-8 text.") from error
    if (reason := refusal_reason(find_hidden(content.decode("utf-8")))) is not None:
        raise PackageError(f"{item.name}: {path}: {reason}")


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
        folder = PurePosixPath(target).parent.as_posix()
        if not layer_has_directory(layer, folder):
            return {}
        return {
            f"{folder}/{path}": content
            for path, content in layer_tree_files(layer, folder, frozenset({SOURCE_FILE})).items()
        }
    return {target: read_layer_bytes(layer, target)} if layer_has_file(layer, target) else {}


def read_record(layer: Layer, name: str) -> dict[str, Any] | None:
    """A package's record, with only the items whose files still exist."""
    file = record_file(layer, name)
    relative = f"{RECORDS_FOLDER}/{name}.json"
    if not layer_has_file(layer, relative):
        return None
    try:
        record = json.loads(read_layer_bytes(layer, relative).decode("utf-8"))
        if not all(isinstance(record.get(key), str) for key in ("name", "repository", "ref", "path", "url")):
            return None
        items = record.get("items") if isinstance(record.get("items"), dict) else {}
        record["items"] = {
            target: entry
            for target, entry in items.items()
            if isinstance(entry, dict) and (layer_has_file(layer, target) or layer_has_directory(layer, target))
        }
        # Items whose files are gone (deleted outside the Blueprint screen); dropped from the file on the next write.
        record[STALE] = [target for target in items if target not in record["items"]]
    except (OSError, ValueError, AttributeError):
        return None
    return record


def read_records(layer: Layer) -> list[dict[str, Any]]:
    if not layer_has_directory(layer, RECORDS_FOLDER):
        return []
    names = sorted(
        PurePosixPath(path).name[:-5]
        for path in layer_tree_files(layer, RECORDS_FOLDER)
        if "/" not in path and path.endswith(".json") and SKILL_NAME.match(path[:-5])
    )
    records = [read_record(layer, name) for name in names]
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
    stored = {key: value for key, value in record.items() if key != STALE}
    write_bytes(layer, str(file.relative_to(layer.root).as_posix()), (json.dumps(stored, indent=2) + "\n").encode())
    return f"{RECORDS_FOLDER}/{file.name}"


def forget_items(layer: Layer, targets: list[str], message: str) -> MutationResult | None:
    """Drop items from the records of their packages, for example after the item was deleted."""
    changed: list[str] = []
    for record in read_records(layer):
        kept = {target: entry for target, entry in record["items"].items() if target not in targets}
        if len(kept) != len(record["items"]) or any(target in record[STALE] for target in targets):
            record["items"] = kept
            changed.append(write_record(layer, record))
    if changed:
        forget_merged()
        return commit_paths(layer, changed, message)
    return None


def stage_item(
    layer: Layer, item: PackageItem, files: dict[str, bytes], url: str, source: Source, name: str, installed_at: str
) -> str:
    if item.kind != "skill":
        for path, content in files.items():
            write_bytes(layer, path, content)
        return item.target

    prefix = f"{PurePosixPath(item.target).parent.as_posix()}/"
    relative = {path[len(prefix) :]: content for path, content in files.items()}
    source_record = {
        "url": url,
        "repository": source.repository,
        "ref": source.ref,
        "commit": source.commit,
        "path": "/".join(part for part in (source.path, item.path) if part),
        "installed_at": installed_at,
        "hash": files_hash(relative),
        "package": name,
    }
    relative[SOURCE_FILE] = (json.dumps(source_record, indent=2) + "\n").encode()
    try:
        replace_folder_contents(layer, PurePosixPath(item.target).parent.as_posix(), relative)
    except OSError as error:
        raise PackageError(f"Could not replace skill '{item.name}': {error}") from error
    return PurePosixPath(item.target).parent.as_posix()


# --- Installing and updating -----------------------------------------------------------------------------------------


def install(
    github: GitHubSkills,
    layer: Layer,
    url: str,
    source: Source,
    choices: list[tuple[str, str, str]],
    action: str = "Install",
) -> PackageInstallResult:
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
        paths.append(stage_item(layer, item, files, url, pinned, name, now))
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
    forget_merged()
    result = commit_paths(
        layer, paths, f"{action} {names} from package {name} ({pinned.repository}@{pinned.commit[:7]}, {layer.label})"
    )
    return PackageInstallResult(targets, result)


def install_from_link(
    github: GitHubSkills, layer: Layer, url: str, ref: str, commit: str, choices: list[tuple[str, str, str]]
) -> PackageInstallResult:
    """Install reviewed items at the reviewed commit; the link and ref are those of the review."""
    link = parse_package_link(url)
    source = Source(link.repository, ref, commit, package_folder(link, ref))
    return install(github, layer, url, source, choices)
