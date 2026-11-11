"""Remember as rule: a correction in a conversation becomes a rule in the Blueprint.

A rule goes into a file the chosen level owns, so it never replaces an inherited file:

- a standard or engineering standard the level itself defines gets the rule in its `rules` list;
- `instructions.md` the level itself defines gets the rule as a line at the end;
- otherwise the level's own rules standard, `standards/rules-<level kind>.md`, created when needed. Each level kind has
  its own file name, so the rules of a lower level never hide those of a higher one.
"""

import difflib

import yaml

from app.blueprint.layers import BlueprintError, Layer, layer_files, parse_document, read_layer_text, validate_path

MAX_RULE_CHARS = 500


def rules_path(layer: Layer) -> str:
    return f"standards/rules-{layer.kind}.md"


def render(front_matter: dict, body: str) -> str:
    header = yaml.safe_dump(front_matter, sort_keys=False, allow_unicode=True, width=1000).strip()
    return f"---\n{header}\n---\n\n{body.strip()}\n"


def target_path(layer: Layer, path: str | None) -> str:
    """Where the rule goes: the given file when this level owns it, else the level's rules standard."""
    if path:
        validate_path(path)
        if path.split("/", 1)[0] in ("standards", "engineering") or path == "instructions.md":
            if path in layer_files(layer):
                return path
    return rules_path(layer)


def with_rule(layer: Layer, path: str, rule: str) -> tuple[str, str]:
    """The file before and after adding the rule."""
    rule = " ".join(rule.split())
    if not rule:
        raise BlueprintError("The rule is empty.")
    if len(rule) > MAX_RULE_CHARS:
        raise BlueprintError(f"Keep a rule under {MAX_RULE_CHARS} characters.")
    before = read_layer_text(layer, path) if path in layer_files(layer) else ""
    if path == "instructions.md":
        return before, f"{before.rstrip()}\n- {rule}\n".lstrip("\n")
    if before:
        document = parse_document(before)
        front = dict(document.front_matter)
        rules = [str(item) for item in front.get("rules") or [] if str(item).strip()]
        if rule in rules:
            raise BlueprintError("This rule is already there.")
        front["rules"] = [*rules, rule]
        return before, render(front, document.body)
    front = {
        "category": "rules",
        "summary": f"Rules remembered from conversations ({layer.label}).",
        "rules": [rule],
    }
    body = "Rules added with Remember as rule in the chat. Edit or remove them here like any standard."
    return before, render(front, body)


def unified_diff(path: str, before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True), after.splitlines(keepends=True), f"a/{path}", f"b/{path}"
        )
    )
