"""Blueprint layers on disk and how they merge.

A level sees its own layer plus the layers above it: global → environment → project. A file at the same relative
path in a lower layer replaces the one above; a file whose front matter says `disabled: true` switches the
inherited item off for that level and below. A skill whose front matter says `extends: true` keeps the inherited
skill and adds its body after it.
"""

import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from app.blueprint import mutations
from app.blueprint.mutations import MutationResult
from app.targets.safe_root import SafeRoot

LayerKind = Literal["global", "environment", "project"]
ItemKind = Literal["instructions", "standard", "engineering", "skill", "command", "agent"]

ITEM_PATH = re.compile(
    r"^(?:instructions\.md"
    r"|(?:standards|engineering|commands|agents)/[a-z0-9][a-z0-9-]{0,59}\.md"
    r"|skills/[a-z0-9][a-z0-9-]{0,59}/SKILL\.md)$"
)
KIND_BY_FOLDER: dict[str, ItemKind] = {
    "standards": "standard",
    "engineering": "engineering",
    "skills": "skill",
    "commands": "command",
    "agents": "agent",
}
PROJECT_LAYER_FOLDER = ".blueprint"
MAX_FILE_BYTES = 512 * 1024


class BlueprintError(Exception):
    """A Blueprint action that cannot be done; the message is safe to show to the user."""


@dataclass(frozen=True)
class Layer:
    kind: LayerKind
    label: str
    root: Path
    # The git repository the layer lives in, and the layer's path inside it.
    repository: Path
    prefix: str = ""


@dataclass(frozen=True)
class Document:
    front_matter: dict[str, Any]
    body: str

    @property
    def disabled(self) -> bool:
        return self.front_matter.get("disabled") is True

    @property
    def extends(self) -> bool:
        return self.front_matter.get("extends") is True


def additions_heading(layer: "Layer") -> str:
    return f"## Additions for {layer.label}"


@dataclass
class Item:
    path: str
    kind: ItemKind
    name: str
    source: Layer
    document: Document
    # The layers above the source that also define this path: what a reset would bring back.
    overrides: list[Layer] = field(default_factory=list)
    # Lower layers that add to this skill instead of replacing it, with their additions, in order.
    extensions: list[tuple[Layer, Document]] = field(default_factory=list)
    # An extension without a skill above to extend: shown with a warning, not used.
    orphan_extension: bool = False

    def text(self) -> str:
        """The full file as the agent loads it: the source file, then each layer's additions."""
        text = read_layer_text(self.source, self.path, errors="replace").rstrip()
        for layer, document in self.extensions:
            text += f"\n\n{additions_heading(layer)}\n\n{document.body.strip()}"
        return text + "\n"

    def without_layer(self, layer: Layer) -> "Item":
        """This item as the layers above `layer` see it: without that layer's additions."""
        kept = [(other, document) for other, document in self.extensions if other != layer]
        base = read_layer_document(self.source, self.path)
        return Item(self.path, self.kind, self.name, self.source, combined(base, kept), self.overrides, kept)

    @property
    def summary(self) -> str:
        value = self.document.front_matter.get("summary") or self.document.front_matter.get("description")
        return str(value).strip() if value else ""

    @property
    def rules(self) -> list[str]:
        value = self.document.front_matter.get("rules")
        return [str(rule).strip() for rule in value if str(rule).strip()] if isinstance(value, list) else []


def validate_path(path: str) -> str:
    if not ITEM_PATH.match(path):
        raise BlueprintError(
            "Unknown Blueprint path. Use instructions.md, standards/<name>.md, engineering/<name>.md, "
            "commands/<name>.md, agents/<name>.md or skills/<name>/SKILL.md (lowercase letters, digits, hyphens)."
        )
    return path


def kind_of(path: str) -> ItemKind:
    if path == "instructions.md":
        return "instructions"
    return KIND_BY_FOLDER[path.split("/", 1)[0]]


def name_of(path: str) -> str:
    if path == "instructions.md":
        return "instructions"
    parts = path.split("/")
    return parts[1] if parts[0] == "skills" else Path(parts[1]).stem


def parse_document(text: str) -> Document:
    """Split optional YAML front matter (between --- lines) from the Markdown body."""
    normalized = text.replace("\r\n", "\n")
    if normalized.startswith("---\n"):
        end = normalized.find("\n---", 4)
        if end != -1:
            try:
                front_matter = yaml.safe_load(normalized[4:end]) or {}
            except yaml.YAMLError as error:
                raise BlueprintError(f"The front matter is not valid YAML: {error}") from error
            if not isinstance(front_matter, dict):
                raise BlueprintError("The front matter must be a set of key: value pairs.")
            body = normalized[end + 4 :].lstrip("\n")
            return Document(front_matter, body)
    return Document({}, normalized)


def read_document(path: Path) -> Document:
    try:
        return parse_document(path.read_text(encoding="utf-8"))
    except BlueprintError:
        # A broken file in one layer must not hide the rest; show it as body only.
        return Document({}, path.read_text(encoding="utf-8", errors="replace"))


def layer_relative(layer: Layer, path: str) -> str:
    """A file relative to the filesystem root used for this layer."""
    return f"{layer.prefix}{path}" if layer.kind == "project" else path


def read_layer_bytes(layer: Layer, path: str) -> bytes:
    relative = layer_relative(layer, path)
    if layer.kind == "project":
        with SafeRoot(layer.repository) as root:
            return root.read_bytes(relative)
    return (layer.root / path).read_bytes()


def read_layer_text(layer: Layer, path: str, errors: str = "strict") -> str:
    return read_layer_bytes(layer, path).decode("utf-8", errors=errors)


def read_layer_document(layer: Layer, path: str) -> Document:
    try:
        text = read_layer_text(layer, path)
    except OSError:
        return Document({}, "")
    except UnicodeDecodeError:
        text = read_layer_text(layer, path, errors="replace")
    try:
        return parse_document(text)
    except BlueprintError:
        return Document({}, text)


def layer_has_file(layer: Layer, path: str) -> bool:
    if layer.kind == "project":
        try:
            with SafeRoot(layer.repository) as root:
                return root.is_file(layer_relative(layer, path))
        except OSError:
            return False
    return (layer.root / path).is_file()


def layer_has_directory(layer: Layer, path: str) -> bool:
    if layer.kind == "project":
        try:
            with SafeRoot(layer.repository) as root:
                return root.is_dir(layer_relative(layer, path))
        except OSError:
            return False
    return (layer.root / path).is_dir()


def layer_tree_files(layer: Layer, path: str, excluded: frozenset[str] = frozenset()) -> dict[str, bytes]:
    """Read regular files below a layer folder without following links outside a project."""
    if layer.kind == "project":
        try:
            with SafeRoot(layer.repository) as root:
                base = layer_relative(layer, path)
                found: dict[str, bytes] = {}
                for relative, info in root.entries(base, recursive=True):
                    if relative.rsplit("/", 1)[-1] not in excluded and root.is_file(relative):
                        suffix = relative.removeprefix(f"{base}/")
                        found[suffix] = root.read_bytes(relative)
                return found
        except OSError:
            return {}
    folder = layer.root / path
    return {
        file.relative_to(folder).as_posix(): file.read_bytes()
        for file in sorted(folder.rglob("*"))
        if file.is_file() and not file.is_symlink() and file.name not in excluded
    }


def layer_files(layer: Layer) -> dict[str, Path]:
    """The layer's item files. Only the folders items can live in are listed, never the whole tree: a layer is a git
    repository, and walking its .git folder on every message made replies slow on a mounted data folder."""
    root = layer.root
    if layer.kind == "project":
        try:
            with SafeRoot(layer.repository) as safe:
                found: dict[str, Path] = {}
                if safe.is_file(layer_relative(layer, "instructions.md")):
                    found["instructions.md"] = root / "instructions.md"
                for folder in ("standards", "engineering", "commands", "agents", "skills"):
                    relative_folder = layer_relative(layer, folder)
                    if not safe.is_dir(relative_folder):
                        continue
                    for entry, info in safe.entries(relative_folder, recursive=False):
                        relative = entry.removeprefix(layer.prefix)
                        if folder == "skills":
                            if not safe.is_dir(entry):
                                continue
                            skill_file = f"{relative}/SKILL.md"
                            if safe.is_file(layer_relative(layer, skill_file)):
                                found[skill_file] = root / skill_file
                        elif Path(relative).suffix == ".md" and safe.is_file(entry):
                            if ITEM_PATH.match(relative):
                                found[relative] = root / relative
                return found
        except OSError:
            return {}
    if not root.is_dir():
        return {}
    candidates: list[Path] = [root / "instructions.md"]
    for folder in ("standards", "engineering", "commands", "agents"):
        if (root / folder).is_dir():
            candidates.extend(entry for entry in (root / folder).iterdir() if entry.suffix == ".md")
    if (root / "skills").is_dir():
        candidates.extend(entry / "SKILL.md" for entry in (root / "skills").iterdir() if entry.is_dir())
    files: dict[str, Path] = {}
    for file in candidates:
        relative = file.relative_to(root).as_posix()
        if ITEM_PATH.match(relative) and file.is_file():
            files[relative] = file
    return files


def combined(base: Document, extensions: list[tuple[Layer, Document]]) -> Document:
    body = base.body.rstrip()
    for layer, document in extensions:
        body += f"\n\n{additions_heading(layer)}\n\n{document.body.strip()}"
    return Document(base.front_matter, body + "\n" if extensions else base.body)


# One reply merges the same layers several times (system prompt, skills, commands, agents, tools). On a data folder
# mounted from the host every listing and read is slow, so a merge is kept for a moment. YAAP's own Blueprint writes
# forget it at once; a change made outside YAAP shows within CACHE_SECONDS.
CACHE_SECONDS = 2.0
_merged: dict[tuple[Layer, ...], tuple[float, list[Item]]] = {}
_merged_lock = threading.Lock()


def forget_merged() -> None:
    with _merged_lock:
        _merged.clear()


def merge_layers(layers: list[Layer]) -> list[Item]:
    """Items as seen from the last layer in the list, including switched-off ones."""
    key = tuple(layers)
    now = time.monotonic()
    with _merged_lock:
        kept = _merged.get(key)
    if kept is not None and now - kept[0] < CACHE_SECONDS:
        return list(kept[1])
    items = read_merged(layers)
    if CACHE_SECONDS > 0:
        with _merged_lock:
            if len(_merged) > 64:
                _merged.clear()
            _merged[key] = (now, items)
    return list(items)


def read_merged(layers: list[Layer]) -> list[Item]:
    merged: dict[str, Item] = {}
    for layer in layers:
        for path, file in layer_files(layer).items():
            previous = merged.get(path)
            document = read_layer_document(layer, path)
            kind = kind_of(path)
            if kind == "skill" and document.extends and not document.disabled:
                if previous is None or previous.orphan_extension:
                    merged[path] = Item(path, kind, name_of(path), layer, document, orphan_extension=True)
                else:
                    extensions = [*previous.extensions, (layer, document)]
                    base = read_layer_document(previous.source, path)
                    merged[path] = Item(
                        path, kind, previous.name, previous.source, combined(base, extensions), previous.overrides, extensions
                    )
                continue
            item = Item(path, kind, name_of(path), layer, document)
            if previous is not None:
                item.overrides = [*previous.overrides, previous.source, *(other for other, _ in previous.extensions)]
            merged[path] = item
    order = {"instructions": 0, "standard": 1, "engineering": 2, "skill": 3, "command": 4, "agent": 5}
    return sorted(merged.values(), key=lambda item: (order[item.kind], item.name))


def active_items(layers: list[Layer]) -> list[Item]:
    return [item for item in merge_layers(layers) if not item.document.disabled and not item.orphan_extension]


def write_file(layer: Layer, path: str, content: str, message: str) -> MutationResult:
    validate_path(path)
    if len(content.encode()) > MAX_FILE_BYTES:
        raise BlueprintError("The file is larger than 512 KB.")
    parse_document(content)  # Reject invalid front matter before anything is written.
    ensure_repository(layer)
    result = mutations.write(layer, path, content, message)
    forget_merged()
    return result


def delete_file(layer: Layer, path: str, message: str) -> MutationResult:
    validate_path(path)
    if not layer_has_file(layer, path):
        raise BlueprintError("This level has no file at that path.")
    ensure_repository(layer)
    if kind_of(path) == "skill":
        # A skill is its whole folder, including the files and source.json of one installed from GitHub.
        result = mutations.delete(layer, str(Path(path).parent.as_posix()), message)
    else:
        result = mutations.delete(layer, path, message)
    forget_merged()
    return result


def ensure_repository(layer: Layer) -> None:
    # Called before every write YAAP makes in a layer.
    forget_merged()
    mutations.ensure_repository(layer)


def commit(layer: Layer, path: str, message: str) -> MutationResult:
    """Commit only this file, so other changes in the repository (for example in a project) are left alone."""
    forget_merged()
    result = mutations.commit_paths(layer, [path], message)
    if not result.committed:
        raise BlueprintError(f"The file was saved, but committing it failed: {result.error}")
    return result
