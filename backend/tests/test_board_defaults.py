"""The definition of done a new card starts with."""

from pathlib import Path

import pytest

from app.blueprint.layers import Layer
from app.board.card import KINDS, parse_checklist
from app.board.defaults import DEFAULT_DEFINITIONS, default_definition


def project_layer(folder: Path, files: dict[str, str]) -> Layer:
    for name, text in files.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return Layer(kind="project", label="Project", root=folder, repository=folder)


@pytest.mark.parametrize("kind", KINDS)
def test_each_kind_starts_with_its_own_unchecked_checklist(kind) -> None:
    items = parse_checklist(default_definition(kind))
    assert [item.text for item in items] == list(DEFAULT_DEFINITIONS[kind])
    assert not any(item.done for item in items)


def test_the_kinds_have_different_definitions() -> None:
    assert len({default_definition(kind) for kind in KINDS}) == len(KINDS)


def test_a_blueprint_standard_for_the_kind_replaces_the_built_in_checklist(tmp_path) -> None:
    layer = project_layer(
        tmp_path, {"standards/definition-of-done-bug.md": "---\nsummary: Bugs\n---\n- [ ] Own bug rule\n"}
    )
    assert default_definition("bug", [layer]) == "- [ ] Own bug rule"
    assert default_definition("feature", [layer]) == default_definition("feature")


def test_an_empty_standard_or_one_for_another_name_leaves_the_built_in_checklist(tmp_path) -> None:
    layer = project_layer(
        tmp_path,
        {
            "standards/definition-of-done-chore.md": "---\nsummary: Chores\n---\n   \n",
            "standards/definition-of-done-other.md": "---\nsummary: Other\n---\n- [ ] x\n",
        },
    )
    assert default_definition("chore", [layer]) == default_definition("chore")


def test_an_unreadable_blueprint_does_not_stop_a_card_from_being_created(tmp_path) -> None:
    gone = tmp_path / "gone"
    missing = Layer(kind="project", label="Gone", root=gone, repository=gone)
    assert default_definition("bug", [missing]) == default_definition("bug")
