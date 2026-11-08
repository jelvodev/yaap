"""Reading the Blueprint quickly: only item folders are listed, and a merge is kept for a moment."""

from pathlib import Path

from app.blueprint import layers
from app.blueprint.layers import Layer, layer_files, merge_layers, write_file


def make_layer(root: Path) -> Layer:
    return Layer("global", "Global", root, root)


def test_only_item_folders_are_listed(tmp_path):
    root = tmp_path / "global"
    (root / "standards").mkdir(parents=True)
    (root / "standards" / "python.md").write_text("Use types.\n", encoding="utf-8")
    (root / "skills" / "caveman").mkdir(parents=True)
    (root / "skills" / "caveman" / "SKILL.md").write_text("Be brief.\n", encoding="utf-8")
    (root / "skills" / "caveman" / "notes.md").write_text("Not an item.\n", encoding="utf-8")
    (root / "instructions.md").write_text("Hello.\n", encoding="utf-8")
    # Markdown in .git or elsewhere is never an item, and is not walked.
    (root / ".git" / "objects" / "ab").mkdir(parents=True)
    (root / ".git" / "objects" / "ab" / "readme.md").write_text("x", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "guide.md").write_text("x", encoding="utf-8")
    assert sorted(layer_files(make_layer(root))) == ["instructions.md", "skills/caveman/SKILL.md", "standards/python.md"]


def test_a_merge_is_kept_briefly_and_forgotten_on_a_write(tmp_path, monkeypatch):
    monkeypatch.setattr(layers, "CACHE_SECONDS", 60.0)
    root = tmp_path / "global"
    (root / "standards").mkdir(parents=True)
    (root / "standards" / "python.md").write_text("Use types.\n", encoding="utf-8")
    layer = make_layer(root)
    assert [item.name for item in merge_layers([layer])] == ["python"]

    # A change outside YAAP shows only when the kept merge expires.
    (root / "standards" / "go.md").write_text("Use gofmt.\n", encoding="utf-8")
    assert [item.name for item in merge_layers([layer])] == ["python"]

    # YAAP's own write forgets the kept merge at once.
    write_file(layer, "standards/rust.md", "Use clippy.\n", "Add rust")
    assert [item.name for item in merge_layers([layer])] == ["go", "python", "rust"]
