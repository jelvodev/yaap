"""Write reviewed import items into Blueprint layers.

The items come from a browser that held untrusted text, so everything is checked again here: key-shaped values,
skill file paths and sizes. Nothing is written until every item has passed.
"""

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Literal

from app.blueprint.importer import BlueprintImportError, item_path, render, slug
from app.blueprint.layers import (
    MAX_FILE_BYTES,
    BlueprintError,
    ItemKind,
    Layer,
    ensure_repository,
    forget_merged,
    parse_document,
    validate_path,
    write_file,
)
from app.blueprint.mutations import MutationResult, replace_folder
from app.blueprint.setup_mapping import clean_extra
from app.blueprint.skill_install import MAX_SKILL_BYTES, MAX_SKILL_FILES, SOURCE_FILE, TEXT_SUFFIXES
from app.secrets.shapes import holds_key_shape, key_refusal, key_shaped_lines

MAX_FILE_PATH = 200


@dataclass(frozen=True)
class ApplyFile:
    path: str
    content: str


@dataclass(frozen=True)
class ApplyItem:
    kind: ItemKind
    name: str
    summary: str
    rules: list[str]
    body: str
    extra: dict[str, Any]
    layer: Literal["level", "global"] = "level"
    files: tuple[ApplyFile, ...] = ()


@dataclass
class ApplyResult:
    written: list[str] = field(default_factory=list)
    commits: list[tuple[str, MutationResult]] = field(default_factory=list)


def file_path_problem(path: str) -> str | None:
    """Why a skill file path is not allowed; None when it stays inside the skill folder."""
    parts = path.split("/")
    if not path or len(path) > MAX_FILE_PATH or "\\" in path or ":" in path:
        return "is not a relative path inside the skill"
    if any(part in ("", ".", "..") or part.lower().startswith(".git") for part in parts):
        return "is not a relative path inside the skill"
    if path.lower() in ("skill.md", SOURCE_FILE):
        return "would replace a file YAAP writes itself"
    if PurePosixPath(path).suffix.lower() not in TEXT_SUFFIXES:
        return "is not a text file"
    return None


def skill_files(item: ApplyItem, label: str) -> dict[str, bytes]:
    """The checked files of a skill as bytes, keyed by their path in the skill folder."""
    if item.files and item.kind != "skill":
        raise BlueprintImportError(f"{label}: only a skill can have files.")
    if len(item.files) > MAX_SKILL_FILES:
        raise BlueprintImportError(f"{label}: a skill has at most {MAX_SKILL_FILES} files.")
    files: dict[str, bytes] = {}
    for file in item.files:
        problem = file_path_problem(file.path)
        if problem is not None:
            raise BlueprintImportError(f"{label}: the file {file.path!r} {problem}.")
        if file.path in files:
            raise BlueprintImportError(f"{label}: the file {file.path} is listed twice.")
        files[file.path] = file.content.encode("utf-8")
        refusal = key_refusal(key_shaped_lines(file.content))
        if refusal is not None:
            raise BlueprintImportError(f"{label}: {file.path}: {refusal}")
    if sum(len(data) for data in files.values()) + len(item.body.encode()) > MAX_SKILL_BYTES:
        raise BlueprintImportError(f"{label}: a skill is at most 5 MB.")
    return files


def checked_items(
    items: list[ApplyItem], level: Layer, global_layer: Layer, known_tools: frozenset[str]
) -> list[tuple[Layer, str, ApplyItem]]:
    planned: list[tuple[Layer, str, ApplyItem]] = []
    seen: set[tuple[Layer, str]] = set()
    for item in items:
        path = item_path(item.kind, slug(item.name))
        target = global_layer if item.layer == "global" else level
        if (target, path) in seen:
            raise BlueprintImportError("Two items would get the same path; rename one.")
        seen.add((target, path))
        refusal = key_refusal(key_shaped_lines(item.body))
        if refusal is not None:
            raise BlueprintImportError(f"{path}: {refusal}")
        if holds_key_shape([item.summary, item.rules, item.extra]):
            raise BlueprintImportError(f"{path}: the summary, a rule or a front matter value looks like a key.")
        _, refused = clean_extra(item.kind, item.extra, known_tools)
        if refused:
            raise BlueprintImportError(f"{path}: front matter {', '.join(refused)} cannot be imported.")
        skill_files(item, path)
        planned.append((target, path, item))
    return planned


def write_item(target: Layer, path: str, item: ApplyItem) -> MutationResult:
    rules = [rule.strip() for rule in item.rules if rule.strip()]
    content = render(item.kind, item.summary.strip(), rules, item.body, item.extra)
    message = f"Import {path} ({target.label})"
    if not item.files:
        return write_file(target, validate_path(path), content, message)
    validate_path(path)
    if len(content.encode()) > MAX_FILE_BYTES:
        raise BlueprintError("The file is larger than 512 KB.")
    parse_document(content)  # Reject invalid front matter before anything is written.
    ensure_repository(target)
    files = {"SKILL.md": content.encode("utf-8"), **skill_files(item, path)}
    try:
        result = replace_folder(target, PurePosixPath(path).parent.as_posix(), files, message)
    except OSError as error:
        raise BlueprintError(f"The skill folder could not be written: {error}") from error
    forget_merged()
    return result


def apply_items(
    items: list[ApplyItem], level: Layer, global_layer: Layer, known_tools: frozenset[str]
) -> ApplyResult:
    """Write the items; each file, or each skill folder, is committed on its own."""
    planned = checked_items(items, level, global_layer, known_tools)
    result = ApplyResult()
    for target, path, item in planned:
        try:
            committed = write_item(target, path, item)
        except BlueprintError as error:
            done = ", ".join(result.written) or "none"
            raise BlueprintImportError(f"{path}: {error} Imported before it: {done}.") from error
        result.written.append(path)
        result.commits.append((path, committed))
    return result
