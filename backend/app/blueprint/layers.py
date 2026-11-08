"""Blueprint layers on disk and how they merge.

A level sees its own layer plus the layers above it: global → environment → project. A file at the same relative
path in a lower layer replaces the one above; a file whose front matter says `disabled: true` switches the
inherited item off for that level and below. A skill whose front matter says `extends: true` keeps the inherited
skill and adds its body after it.
"""

import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from app.git.service import GitError, init_repository, run_git

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
        text = (self.source.root / self.path).read_text(encoding="utf-8", errors="replace").rstrip()
        for layer, document in self.extensions:
            text += f"\n\n{additions_heading(layer)}\n\n{document.body.strip()}"
        return text + "\n"

    def without_layer(self, layer: Layer) -> "Item":
        """This item as the layers above `layer` see it: without that layer's additions."""
        kept = [(other, document) for other, document in self.extensions if other != layer]
        base = read_document(self.source.root / self.path)
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


def layer_files(layer: Layer) -> dict[str, Path]:
    """The layer's item files. Only the folders items can live in are listed, never the whole tree: a layer is a git
    repository, and walking its .git folder on every message made replies slow on a mounted data folder."""
    root = layer.root
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
            document = read_document(file)
            kind = kind_of(path)
            if kind == "skill" and document.extends and not document.disabled:
                if previous is None or previous.orphan_extension:
                    merged[path] = Item(path, kind, name_of(path), layer, document, orphan_extension=True)
                else:
                    extensions = [*previous.extensions, (layer, document)]
                    base = read_document(previous.source.root / path)
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


def write_file(layer: Layer, path: str, content: str, message: str) -> None:
    validate_path(path)
    if len(content.encode()) > MAX_FILE_BYTES:
        raise BlueprintError("The file is larger than 512 KB.")
    parse_document(content)  # Reject invalid front matter before anything is written.
    ensure_repository(layer)
    target = layer.root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content.replace("\r\n", "\n"), encoding="utf-8")
    forget_merged()
    commit(layer, path, message)


def delete_file(layer: Layer, path: str, message: str) -> None:
    validate_path(path)
    target = layer.root / path
    if not target.is_file():
        raise BlueprintError("This level has no file at that path.")
    forget_merged()
    if kind_of(path) == "skill":
        # A skill is its whole folder, including the files and source.json of one installed from GitHub.
        shutil.rmtree(target.parent)
        commit(layer, str(Path(path).parent.as_posix()), message)
        return
    target.unlink()
    if target.parent != layer.root and not any(target.parent.iterdir()):
        target.parent.rmdir()
    commit(layer, path, message)


def ensure_repository(layer: Layer) -> None:
    # Called before every write YAAP makes in a layer.
    forget_merged()
    layer.root.mkdir(parents=True, exist_ok=True)
    if layer.kind != "project":
        init_repository(layer.repository)


def commit(layer: Layer, path: str, message: str) -> None:
    """Commit only this file, so other changes in the repository (for example in a project) are left alone."""
    pathspec = f"{layer.prefix}{path}"
    forget_merged()
    try:
        run_git(["add", "--all", "--", pathspec], layer.repository)
        status = run_git(["status", "--porcelain", "--", pathspec], layer.repository).stdout.strip()
        if status:
            run_git(["commit", "-m", message, "--", pathspec], layer.repository)
    except GitError as error:
        raise BlueprintError(f"The file was saved, but committing it failed: {error}") from error
