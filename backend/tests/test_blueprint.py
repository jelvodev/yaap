import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from app.blueprint import mutations
from app.blueprint.layers import BlueprintError, Layer, layer_files, parse_document, read_merged
from app.git.service import GitError, GitResult, init_repository, run_git
from app.providers.fake_provider import ScriptedResponse

CODING = """---
summary: Simple, modular code with descriptive names.
rules:
  - Python uses snake_case.
  - English in code.
---

The full coding standard.
"""


def put(client, path: str, content: str, **level):
    return client.put("/api/v1/blueprint/file", params={"path": path, **level}, json={"content": content})


def items(client, **level) -> dict[str, dict]:
    return {item["path"]: item for item in client.get("/api/v1/blueprint/items", params=level).json()}


def commits(folder: Path) -> list[str]:
    output = subprocess.run(["git", "log", "--format=%s"], cwd=folder, capture_output=True, text=True, check=True)
    return output.stdout.splitlines()


def test_front_matter_is_parsed():
    document = parse_document(CODING)
    assert document.front_matter["summary"].startswith("Simple")
    assert document.body.startswith("The full coding standard.")
    with pytest.raises(BlueprintError):
        parse_document("---\n[unclosed\n---\nbody")


@pytest.mark.skipif(__import__("os").name != "posix", reason="symlink checks require POSIX")
def test_project_blueprint_layer_does_not_read_links_outside_project(tmp_path: Path):
    project = tmp_path / "project"
    layer_root = project / ".blueprint"
    standards = layer_root / "standards"
    standards.mkdir(parents=True)
    internal = standards / "inside.md"
    internal.write_text("Inside standard.", encoding="utf-8")
    external = tmp_path / "external.md"
    external.write_text("external canary", encoding="utf-8")
    (standards / "internal-link.md").symlink_to(internal)
    (standards / "external-link.md").symlink_to(external)
    (layer_root / "skills").mkdir()
    (layer_root / "skills" / "external-skill").symlink_to(external.parent, target_is_directory=True)
    layer = Layer("project", "Project", layer_root, project, ".blueprint/")

    found = layer_files(layer)
    assert set(found) == {"standards/inside.md", "standards/internal-link.md"}
    merged = read_merged([layer])
    assert [item.text() for item in merged] == ["Inside standard.\n", "Inside standard.\n"]


@pytest.mark.skipif(__import__("os").name != "posix", reason="symlink checks require POSIX")
def test_project_blueprint_mutations_cannot_follow_links_into_git_or_outside(tmp_path: Path):
    project = tmp_path / "project"
    layer_root = project / ".blueprint"
    layer_root.mkdir(parents=True)
    git_config = project / ".git" / "config"
    git_config.parent.mkdir()
    git_config.write_text("safe", encoding="utf-8")
    (layer_root / "git-config.md").symlink_to(git_config)
    outside = tmp_path / "outside"
    outside.mkdir()
    (layer_root / "linked").symlink_to(outside, target_is_directory=True)
    layer = Layer("project", "Project", layer_root, project, ".blueprint/")

    with pytest.raises(OSError):
        mutations.write_bytes(layer, "git-config.md", b"unsafe")
    with pytest.raises(OSError):
        mutations.write_bytes(layer, "linked/new.md", b"unsafe")
    assert git_config.read_text(encoding="utf-8") == "safe"
    assert not (outside / "new.md").exists()


def test_global_item_is_inherited_by_environments(client, tmp_path):
    assert put(client, "engineering/coding.md", CODING).status_code == 200

    global_view = items(client)["engineering/coding.md"]
    assert global_view["defined_here"] is True
    assert global_view["rules"] == ["Python uses snake_case.", "English in code."]

    work_view = items(client, environment_id=2)["engineering/coding.md"]
    assert work_view["defined_here"] is False
    assert work_view["source"] == "global"
    assert work_view["inherited_from"] == "Global"
    assert commits(Path(tmp_path) / "blueprint" / "global") == ["Update engineering/coding.md (Global)"]


def test_environment_override_and_reset(client):
    put(client, "engineering/coding.md", CODING)
    put(client, "engineering/coding.md", "---\nsummary: Work coding rules.\n---\nWork body.", environment_id=2)

    work_view = items(client, environment_id=2)["engineering/coding.md"]
    assert work_view["source"] == "environment"
    assert work_view["summary"] == "Work coding rules."
    assert work_view["inherited_from"] == "Global"
    # Private still sees the global version.
    assert items(client, environment_id=1)["engineering/coding.md"]["summary"].startswith("Simple")

    removed = client.delete("/api/v1/blueprint/file", params={"path": "engineering/coding.md", "environment_id": 2})
    assert removed.status_code == 200 and removed.json()["statuses"][0]["committed"] is True
    assert items(client, environment_id=2)["engineering/coding.md"]["source"] == "global"


def test_switch_off_only_for_inherited_items(client):
    put(client, "standards/security.md", "---\nsummary: Secure by default.\n---\n")
    switched = client.post("/api/v1/blueprint/switch-off", params={"path": "standards/security.md", "environment_id": 1})
    assert switched.status_code == 200
    assert switched.json()["disabled"] is True
    assert items(client, environment_id=2)["standards/security.md"]["disabled"] is False

    own = client.post("/api/v1/blueprint/switch-off", params={"path": "standards/security.md"})
    assert own.status_code == 409


def test_project_layer_lives_in_the_project_repository(client, tmp_path):
    project = client.post("/api/v1/environments/1/projects", json={"name": "Site"}).json()
    response = put(client, "instructions.md", "Use Astro.", environment_id=1, project_id=project["id"])
    assert response.status_code == 200
    folder = Path(tmp_path) / "workspace" / project["folder"]
    assert (folder / ".blueprint" / "instructions.md").read_text(encoding="utf-8") == "Use Astro."
    assert commits(folder) == ["Update instructions.md (Site)"]


def test_invalid_paths_and_front_matter_are_refused(client):
    assert put(client, "../escape.md", "x").status_code == 422
    assert put(client, "standards/Bad Name.md", "x").status_code == 422
    assert put(client, "standards/ok.md", "---\n[unclosed\n---\n").status_code == 422


def test_save_reports_commit_failure_and_a_later_save_commits_the_file(client, monkeypatch, tmp_path):
    root = Path(tmp_path) / "blueprint" / "global"
    real_run_git = mutations.run_git

    def fail_commit(arguments: Sequence[str], cwd: Path) -> GitResult:
        if arguments[0] == "commit":
            raise GitError("injected commit failure")
        return real_run_git(arguments, cwd)

    with monkeypatch.context() as patcher:
        patcher.setattr(mutations, "run_git", fail_commit)
        failed = put(client, "standards/retry.md", "Saved content.")

    assert failed.status_code == 200
    assert failed.json()["mutation"] == {
        "path": "standards/retry.md",
        "committed": False,
        "error": "injected commit failure",
    }
    assert (root / "standards" / "retry.md").read_text(encoding="utf-8") == "Saved content."

    retried = put(client, "standards/retry.md", "Saved content.")
    assert retried.status_code == 200 and retried.json()["mutation"]["committed"] is True
    assert run_git(["show", "HEAD:standards/retry.md"], root).stdout == "Saved content."


def test_blueprint_commit_leaves_an_unrelated_staged_file_alone(tmp_path):
    repository = Path(tmp_path) / "repository"
    layer_root = repository / ".blueprint"
    repository.mkdir()
    init_repository(repository)
    layer = Layer("project", "Site", layer_root, repository, ".blueprint/")
    unrelated = repository / "unrelated.txt"
    unrelated.write_text("keep staged", encoding="utf-8")
    run_git(["add", "--", "unrelated.txt"], repository)

    result = mutations.write(layer, "instructions.md", "Use the project standard.\n", "Save Blueprint")

    assert result.committed is True
    assert run_git(["diff", "--cached", "--name-only"], repository).stdout.strip() == "unrelated.txt"
    assert run_git(["show", "--name-only", "--format=", "HEAD"], repository).stdout.strip() == ".blueprint/instructions.md"


def test_blueprint_reaches_the_system_prompt(client, fake):
    put(client, "instructions.md", "Always propose tests.")
    put(client, "engineering/coding.md", CODING)
    put(client, "skills/release/SKILL.md", "---\ndescription: How to cut a release.\n---\nSteps.")
    put(client, "standards/legacy.md", "---\nsummary: Old rule.\n---\n")
    client.post("/api/v1/blueprint/switch-off", params={"path": "standards/legacy.md", "environment_id": 1})

    client.put("/api/v1/environments/1/credentials/anthropic_api_key", json={"value": "sk-test-valid-key-1234"})
    conversation_id = client.post(
        "/api/v1/environments/1/conversations", json={"model": "claude-opus-5-5", "effort": "medium"}
    ).json()["id"]
    fake.responses.append(ScriptedResponse(text="OK"))
    client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Hallo"})

    system = "\n\n".join(part.text for part in fake.requests[0].system)
    assert "You are YAAP" in system
    assert "Always propose tests." in system
    assert "## coding (engineering standard)" in system and "- Python uses snake_case." in system
    assert "- skill `release`: How to cut a release." in system
    assert "Old rule." not in system
    # The full body of a standard is not in the prompt; it is loaded on demand.
    assert "The full coding standard." not in system
    assert fake.requests[0].system[-1].cacheable is True


# --- Extending a skill instead of replacing it ---------------------------------------------------------

RELEASE = "---\ndescription: How to cut a release.\n---\nTag, build, publish.\n"


def test_a_project_can_extend_an_inherited_skill(client, tmp_path):
    from app.blueprint.layers import active_items
    from app.blueprint.levels import layers_for

    project = client.post("/api/v1/environments/1/projects", json={"name": "Site"}).json()
    level = {"environment_id": 1, "project_id": project["id"]}
    put(client, "skills/release/SKILL.md", RELEASE)
    put(client, "skills/release/SKILL.md", "---\nextends: true\n---\nAlso bump the changelog.", environment_id=1)
    response = put(client, "skills/release/SKILL.md", "---\nextends: true\n---\nDeploy to Netlify.", **level)
    assert response.status_code == 200
    body = response.json()
    assert body["content"].endswith("Deploy to Netlify.")
    assert "Also bump the changelog." in body["inherited_content"]
    assert "Deploy to Netlify." not in body["inherited_content"]

    item = items(client, **level)["skills/release/SKILL.md"]
    assert item["source"] == "global" and item["extended_by"] == ["Private", "Site"]
    assert item["extended_here"] is True and item["summary"] == "How to cut a release."
    assert items(client)["skills/release/SKILL.md"]["extended_by"] == []

    from app.storage.models import Environment, Project

    with client.app.state.session_factory() as session:
        layers = layers_for(tmp_path / "blueprint", tmp_path / "workspace", session.get(Environment, 1), session.get(Project, project["id"]))
    skill = next(item for item in active_items(layers) if item.name == "release")
    text = skill.text()
    assert text.index("Tag, build, publish.") < text.index("## Additions for Private") < text.index("## Additions for Site")
    assert "Deploy to Netlify." in skill.document.body

    # The original changing above reaches the project, with the additions still after it.
    put(client, "skills/release/SKILL.md", RELEASE.replace("publish", "publish to PyPI"))
    assert "publish to PyPI" in client.get("/api/v1/blueprint/file", params={"path": "skills/release/SKILL.md", **level}).json()["inherited_content"]

    # Reset removes only the project's additions.
    removed = client.delete("/api/v1/blueprint/file", params={"path": "skills/release/SKILL.md", **level})
    assert removed.status_code == 200 and removed.json()["statuses"][0]["committed"] is True
    assert items(client, **level)["skills/release/SKILL.md"]["extended_by"] == ["Private"]


def test_an_extension_without_an_original_is_not_used(client):
    put(client, "skills/ghost/SKILL.md", "---\nextends: true\n---\nNothing to extend.", environment_id=1)
    item = items(client, environment_id=1)["skills/ghost/SKILL.md"]
    assert item["orphan_extension"] is True
    # A switch-off in a lower level still wins over an extension above it.
    put(client, "skills/release/SKILL.md", RELEASE)
    put(client, "skills/release/SKILL.md", "---\nextends: true\n---\nMore.", environment_id=1)
    client.post("/api/v1/blueprint/switch-off", params={"path": "skills/release/SKILL.md", "environment_id": 2})
    assert items(client, environment_id=2)["skills/release/SKILL.md"]["disabled"] is True
