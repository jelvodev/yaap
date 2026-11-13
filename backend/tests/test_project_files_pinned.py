"""A project's files stay the folder the run pinned: a swapped root, a linked parent or a linked Blueprint folder never
reaches files outside it."""

import asyncio
import os
import subprocess
from pathlib import Path

import pytest

from app.agent.runs import RunConfig, RunRegistry
from app.agent.tool_context import files, project_root
from app.agent.toolkit import ToolError
from app.agent.tools import tools_for
from app.blueprint.layers import BlueprintError, Layer
from app.checkpoints.shadow import ShadowRepository
from app.learning.changes import with_skill_step
from app.targets.safe_root import RootReplacedError, SafeRoot


def link_or_skip(link: Path, target: Path) -> None:
    """Make a directory link, or skip where the platform does not allow it (Windows without the privilege)."""
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are unavailable")


def start_run(folder: Path):  # noqa: ANN201 # the run type is private to the registry
    return RunRegistry().start(
        RunConfig(
            conversation_id=1,
            environment_id=1,
            project_id=1,
            project_folder=folder,
            blueprint_layers=[],
            tool_names=tools_for(has_project=True, has_attachments=False),
            git_author=None,
            github_token=None,
        )
    )


def swap_for_another_folder(root: Path, moved: Path) -> None:
    root.rename(moved)
    root.mkdir()
    (root / "file.txt").write_text("swapped", encoding="utf-8")


def test_a_project_root_swapped_between_two_tool_calls_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "file.txt").write_text("pinned", encoding="utf-8")
    run = start_run(root)
    assert project_root(run) == root
    assert asyncio.run(files(run).read_bytes("file.txt")) == b"pinned"

    swap_for_another_folder(root, tmp_path / "moved")

    with pytest.raises(ToolError, match="replaced"):
        project_root(run)
    with pytest.raises(ToolError, match="replaced"):
        files(run)


@pytest.mark.skipif(os.name != "posix", reason="descriptor-relative checks require POSIX")
def test_a_project_root_swapped_after_the_check_is_refused_when_the_files_open(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    run = start_run(root)
    pin = run.project_identity
    assert pin is not None

    swap_for_another_folder(root, tmp_path / "moved")

    with pytest.raises(RootReplacedError):
        SafeRoot(root, pin)


def test_a_restore_leaves_an_outside_file_alone_when_a_parent_folder_became_a_link(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    (work / "keep.txt").write_text("kept", encoding="utf-8")
    shadow = ShadowRepository(tmp_path / "shadow.git", work)
    target = shadow.snapshot_tree()
    (work / "sub").mkdir()
    (work / "sub" / "added.txt").write_text("added", encoding="utf-8")
    current = shadow.snapshot_tree()

    # The added file's folder is replaced by a link to a folder outside the project that holds a file of the same name.
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = outside / "added.txt"
    canary.write_text("canary", encoding="utf-8")
    (work / "sub" / "added.txt").unlink()
    (work / "sub").rmdir()
    link_or_skip(work / "sub", outside)

    shadow.restore(target, current)

    assert canary.read_text(encoding="utf-8") == "canary"
    assert outside.is_dir()
    assert (work / "keep.txt").read_text(encoding="utf-8") == "kept"


def test_a_learning_skill_step_on_a_project_layer_whose_folder_is_a_link_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    skill = outside / "skills" / "review" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: review\n---\n- look\n", encoding="utf-8")
    subprocess.run(["git", "init", "--quiet", str(outside)], check=True)
    link = tmp_path / "project-blueprint"
    link_or_skip(link, outside)
    layer = Layer(kind="project", label="Project", root=link, repository=link, environment_id=1)

    with pytest.raises(BlueprintError):
        with_skill_step([layer], "skills/review/SKILL.md", "check the tests")

    assert skill.read_text(encoding="utf-8") == "---\nname: review\n---\n- look\n"
