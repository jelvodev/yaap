"""What a learning proposal changes in a Blueprint layer: the file before and
after, computed from the layer as it is now.

- A **rule** goes where *Remember as rule* puts it (`blueprint.rules`): a standard the level owns, or the level's own
  rules standard.
- A **skill step** is added to a skill's body as a list item. A skill the level defines itself gets the line at the
  end; a skill inherited from a level above gets it through an extension in the level's own layer (`extends: true`),
  so the inherited skill is never replaced.
- A **rule removal** takes a rule out of the `rules` list of the standard it is in (or the line out of
  `instructions.md`).

The same functions serve the branch a proposal waits on and the commit that accepts it, so an accepted proposal is
the measured change applied to the layer as it is at that moment.
"""

from dataclasses import dataclass
from typing import Literal

from app.blueprint.layers import (
    BlueprintError,
    Layer,
    layer_files,
    merge_layers,
    parse_document,
    validate_path,
)
from app.blueprint.rules import MAX_RULE_CHARS, render, target_path, with_rule

BlueprintKind = Literal["rule", "skill_step", "rule_removal"]


@dataclass(frozen=True)
class FileChange:
    path: str
    before: str
    after: str


def skill_path(name: str) -> str:
    return validate_path(f"skills/{name}/SKILL.md")


def clean_line(text: str) -> str:
    line = " ".join(text.split())
    if not line:
        raise BlueprintError("The text is empty.")
    if len(line) > MAX_RULE_CHARS:
        raise BlueprintError(f"Keep it under {MAX_RULE_CHARS} characters.")
    return line


def with_skill_step(layers: list[Layer], path: str, step: str) -> FileChange:
    """The skill at path with the step added, in the last layer of `layers` (the level's own)."""
    step = clean_line(step)
    own = layers[-1]
    file = own.root / path
    if path in layer_files(own):
        before = file.read_text(encoding="utf-8")
        if f"- {step}" in before.splitlines():
            raise BlueprintError("This step is already in the skill.")
        return FileChange(path, before, f"{before.rstrip()}\n- {step}\n")
    if not any(item.path == path and not item.document.disabled for item in merge_layers(layers[:-1])):
        raise BlueprintError(f"No level above has the skill {path.split('/')[1]}.")
    front = {"extends": True}
    return FileChange(path, "", render(front, f"- {step}"))


def without_rule(layer: Layer, path: str, rule: str) -> FileChange:
    """The file with the rule taken out; BlueprintError when this level's file does not have it."""
    validate_path(path)
    rule = " ".join(rule.split())
    file = layer.root / path
    if path not in layer_files(layer):
        raise BlueprintError(f"{layer.label} has no file {path}.")
    before = file.read_text(encoding="utf-8")
    if path == "instructions.md":
        lines = before.replace("\r\n", "\n").split("\n")
        kept = [line for line in lines if " ".join(line.lstrip("-* ").split()) != rule]
        if len(kept) == len(lines):
            raise BlueprintError("The rule is not in this file.")
        return FileChange(path, before, "\n".join(kept))
    document = parse_document(before)
    front = dict(document.front_matter)
    rules = [str(item) for item in front.get("rules") or []]
    kept_rules = [item for item in rules if " ".join(item.split()) != rule]
    if len(kept_rules) == len(rules):
        raise BlueprintError("The rule is not in this file.")
    front["rules"] = kept_rules
    return FileChange(path, before, render(front, document.body))


def blueprint_change(kind: BlueprintKind, layers: list[Layer], target: str, text: str) -> FileChange:
    """The change of a Blueprint proposal in the last layer of `layers` (its level's own layer, with the layers above
    it before)."""
    own = layers[-1]
    if kind == "rule":
        path = target_path(own, target or None)
        before, after = with_rule(own, path, text)
        return FileChange(path, before, after)
    if kind == "skill_step":
        return with_skill_step(layers, target, text)
    return without_rule(own, target, text)
