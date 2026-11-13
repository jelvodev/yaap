"""The definition of done a new card starts with: a checklist per kind, replaceable from the Blueprint."""

from collections.abc import Sequence

from app.blueprint.layers import BlueprintError, Layer, active_items
from app.board.card import Kind

DEFAULT_DEFINITIONS: dict[Kind, tuple[str, ...]] = {
    "bug": (
        "The bug is reproduced by a test that fails before the fix",
        "The test passes after the fix",
        "No other tests fail",
        "The cause is explained in the evidence",
    ),
    "feature": (
        "The acceptance criteria in the specification are met",
        "Tests cover the new behaviour",
        "No existing tests fail",
        "Documentation is updated where users see the change",
    ),
    "refactor": (
        "Behaviour is unchanged: the tests pass before and after",
        "The code is simpler or better structured than before",
        "No new features or fixes are mixed in",
    ),
    "chore": (
        "The work described is done",
        "The tests still pass",
        "Anything the change touches in documentation or configuration is updated",
    ),
}


def default_definition(kind: Kind, layers: Sequence[Layer] = ()) -> str:
    """The definition of done as a Markdown checklist; a Blueprint standard `definition-of-done-<kind>` replaces it."""
    if layers:
        try:
            items = active_items(list(layers))
        except (BlueprintError, OSError):
            # An unreadable Blueprint must not stop a card from being created; the built-in checklist applies.
            items = []
        for item in items:
            if item.kind == "standard" and item.name == f"definition-of-done-{kind}":
                body = item.document.body.strip()
                if body:
                    return body
    return "\n".join(f"- [ ] {line}" for line in DEFAULT_DEFINITIONS[kind])
