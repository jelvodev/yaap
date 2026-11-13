"""Blueprint operations on one level that compose file writes with package records: save, switch off, reset and
delete, remembering a rule, and the existence checks that come before a GitHub or package action."""

from dataclasses import dataclass

from app.blueprint import packages, skill_install
from app.blueprint.errors import BlueprintConflictError, BlueprintNotFoundError
from app.blueprint.layers import BlueprintError, Item, Layer, delete_file, merge_layers, validate_path, write_file
from app.blueprint.mutations import MutationResult
from app.blueprint.rules import target_path, unified_diff, with_rule
from app.blueprint.skill_install import Origin, read_origin

SWITCHED_OFF = "---\ndisabled: true\n---\n"


@dataclass(frozen=True)
class RemovedFile:
    """The file removal and, when the file belonged to a package, the update of that package's record."""

    file: MutationResult
    package_record: MutationResult | None


@dataclass(frozen=True)
class RuleChange:
    """A rule added to a file the level owns; `mutation` is set only when it was applied."""

    path: str
    diff: str
    mutation: MutationResult | None


def find_item(layers: list[Layer], path: str) -> Item | None:
    return next((item for item in merge_layers(layers) if item.path == path), None)


def save_file(own: Layer, path: str, content: str) -> MutationResult:
    """Create the file at this level, or override an inherited one."""
    return write_file(own, validate_path(path), content, f"Update {path} ({own.label})")


def switch_off(layers: list[Layer], path: str) -> tuple[Item, MutationResult]:
    """Switch an inherited item off for this level and below, without touching the level that defines it."""
    own = layers[-1]
    item = find_item(layers, validate_path(path))
    if item is None or (item.source == own and not item.overrides):
        raise BlueprintConflictError(
            "Only an inherited item can be switched off. Delete an item of this level instead."
        )
    result = write_file(own, path, SWITCHED_OFF, f"Switch off {path} ({own.label})")
    switched = find_item(layers, path)
    assert switched is not None
    return switched, result


def remove_file(own: Layer, path: str) -> RemovedFile:
    """Remove this level's file and forget it in its package: an override or switch-off is reset to inherited; an
    own item is deleted."""
    result = delete_file(own, validate_path(path), f"Remove {path} ({own.label})")
    record_result = packages.forget_items(own, [path], f"Forget {path} in its package ({own.label})")
    return RemovedFile(result, record_result)


def remember_rule(own: Layer, rule: str, path: str | None, apply: bool) -> RuleChange:
    """Add a rule to a file this level owns: preview the diff, or apply it as one commit."""
    result: MutationResult | None = None
    target = target_path(own, path)
    before, after = with_rule(own, target, rule)
    if apply:
        result = write_file(own, target, after, f"Remember rule: {' '.join(rule.split())[:60]} ({own.label})")
    return RuleChange(target, unified_diff(target, before, after), result)


def check_distinct_names(names: list[str]) -> None:
    if len(set(names)) != len(names):
        raise BlueprintError("Two skills would get the same name; rename one.")


def installed_skill_origin(own: Layer, name: str) -> Origin:
    """Where a skill of this level was installed from; raises when it was not installed from GitHub."""
    origin = read_origin(own, name) if skill_install.SKILL_NAME.match(name) else None
    if origin is None:
        raise BlueprintNotFoundError("This level has no skill with that name installed from GitHub.")
    return origin


def require_installed_package(own: Layer, name: str) -> None:
    if not skill_install.SKILL_NAME.match(name) or packages.read_record(own, name) is None:
        raise BlueprintNotFoundError("This level has no package with that name.")
