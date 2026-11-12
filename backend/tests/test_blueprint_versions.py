"""The version of a Blueprint layer: its git commit and whether it has uncommitted changes."""

import subprocess
from pathlib import Path

import pytest

from app.blueprint.layers import Layer
from app.blueprint.versions import (
    LayerVersion,
    layer_version,
    layer_versions,
    parse_layer_status,
    versions_from_json,
)
from app.git.service import commit_all

SHA = "3f2a1bc9d0e4a5b6c7d8e9f00112233445566778"
HEADERS = f"# branch.oid {SHA}\n# branch.head main\n"


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        (HEADERS, (SHA, False)),
        (HEADERS + "1 .M N... 100644 100644 100644 aaa bbb .blueprint/instructions.md\n", (SHA, True)),
        (HEADERS + "? .blueprint/standards/new.md\n", (SHA, True)),
        ("# branch.oid (initial)\n# branch.head main\n", (None, False)),
        ("# branch.oid (initial)\n? file.md\n", (None, True)),
        ("", (None, False)),
    ],
    ids=["clean", "tracked change", "untracked file", "no commit yet", "no commit yet with a file", "empty"],
)
def test_the_status_output_gives_the_commit_and_whether_the_layer_has_changes(output, expected):
    assert parse_layer_status(output) == expected


def test_a_layer_outside_a_git_repository_has_no_commit_and_runs_no_git(tmp_path, monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("git must not run")

    monkeypatch.setattr("app.blueprint.versions.run_git", refuse)
    layer = Layer("project", "Copy", tmp_path, tmp_path, "", None)

    assert layer_version(layer) == LayerVersion("project", None, False)


def git(folder: Path, *arguments: str) -> None:
    subprocess.run(["git", *arguments], cwd=folder, check=True, capture_output=True)


def test_a_layer_in_a_repository_gives_its_commit_and_whether_it_changed(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "--local", "user.name", "Tester")
    git(tmp_path, "config", "--local", "user.email", "tester@example.com")
    rules = tmp_path / ".blueprint"
    rules.mkdir()
    (rules / "instructions.md").write_text("Be brief.\n", encoding="utf-8")
    (tmp_path / "other.txt").write_text("outside the layer\n", encoding="utf-8")
    commit_all(tmp_path, "Start")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout.strip()
    layer = Layer("project", "Project", rules, tmp_path, ".blueprint/", None)

    assert layer_version(layer) == LayerVersion("project", head, False)
    # A change outside the layer's folder is not the layer's.
    (tmp_path / "other.txt").write_text("changed\n", encoding="utf-8")
    assert layer_version(layer).dirty is False
    (rules / "instructions.md").write_text("Be brief and kind.\n", encoding="utf-8")
    assert layer_version(layer) == LayerVersion("project", head, True)
    assert layer_versions([layer, Layer("global", "Global", tmp_path / "none", tmp_path / "none", "", None)]) == [
        LayerVersion("project", head, True),
        LayerVersion("global", None, False),
    ]


def test_stored_versions_skip_missing_and_invalid_entries():
    stored = [
        {"kind": "global", "commit": SHA, "dirty": False},
        {"kind": "project", "commit": None, "dirty": True},
        {"kind": "nowhere", "commit": SHA, "dirty": False},
        {"kind": "project", "commit": 5, "dirty": False},
        {"kind": "project", "commit": SHA},
        "text",
    ]

    assert versions_from_json(stored) == [LayerVersion("global", SHA, False), LayerVersion("project", None, True)]
    assert versions_from_json(None) == []
