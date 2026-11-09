"""YAAP's starter Blueprint: the ref it reads, the links of its packs, their state per level, and moving to a newer
version's tag, from a fake GitHub."""

import json
from pathlib import Path

import pytest

from app.api import blueprint_starter
from app.blueprint import skill_install, starter
from app.blueprint.layers import Layer
from app.blueprint.skill_install import GitHubSkills
from tests.test_blueprint_packages import COMMIT_A, COMMIT_B, FakeGitHub

CORE = "blueprint/starter/core"
MANIFEST = {
    "name": "yaap-core",
    "description": "YAAP's starter core.",
    "items": [
        {"kind": "standard", "path": "standards/working-method.md"},
        {"kind": "command", "path": "commands/debug.md"},
    ],
}
WORKING = b"---\nsummary: How to work.\nrules:\n  - Read before you change.\n---\n\nThe method.\n"
FILES = {
    "README.md": b"# YAAP\n",
    f"{CORE}/blueprint-package.json": json.dumps(MANIFEST).encode(),
    f"{CORE}/standards/working-method.md": WORKING,
    f"{CORE}/commands/debug.md": b"Find the cause before the fix.\n",
}


@pytest.fixture
def github(monkeypatch) -> FakeGitHub:
    fake = FakeGitHub(starter.STARTER_REPOSITORY)
    fake.files = {COMMIT_A: dict(FILES)}
    fake.refs = {"main": COMMIT_A, "v0.5.6": COMMIT_A}
    monkeypatch.setattr(skill_install, "http_get", fake)
    return fake


@pytest.mark.parametrize(
    ("tags", "version", "expected"),
    [
        (["v0.5.6"], "0.5.6", starter.StarterRef("v0.5.6", True)),
        (["v0.5.6", "v0.5.7"], "0.5.7", starter.StarterRef("v0.5.7", True)),
        # A development build has no tag of its own yet: the development branch, not pinned.
        (["v0.5.6"], "0.5.7", starter.StarterRef("main", False)),
        ([], "0.5.6", starter.StarterRef("main", False)),
    ],
)
def test_the_ref_is_the_version_tag_or_else_main(
    tags: list[str], version: str, expected: starter.StarterRef
) -> None:
    fake = FakeGitHub(starter.STARTER_REPOSITORY)
    fake.refs = {"main": COMMIT_A, **{tag: COMMIT_A for tag in tags}}
    assert starter.starter_ref(GitHubSkills(None, get=fake), version) == expected


def test_every_pack_links_to_its_folder_at_the_ref() -> None:
    assert [pack.key for pack in starter.STARTER_PACKS] == ["core", "web", "python", "data", "devops", "writing"]
    assert [pack.name for pack in starter.STARTER_PACKS] == [f"yaap-{pack.key}" for pack in starter.STARTER_PACKS]
    assert [pack.key for pack in starter.STARTER_PACKS if pack.chosen] == ["core"]
    assert starter.pack_url(starter.CORE_PACK, "v0.5.7") == (
        "https://github.com/jelvodev/yaap/tree/v0.5.7/blueprint/starter/core"
    )
    assert starter.credits_url("main") == "https://github.com/jelvodev/yaap/blob/main/docs/credits.md"


def write_record(root: Path, repository: str, ref: str, with_file: bool) -> None:
    (root / "packages").mkdir(parents=True)
    target = "standards/working-method.md"
    record = {
        "name": "yaap-core",
        "repository": repository,
        "ref": ref,
        "path": CORE,
        "url": f"https://github.com/{repository}/tree/{ref}/{CORE}",
        "items": {target: {"kind": "standard", "source": target, "name": "working-method", "commit": COMMIT_A}},
    }
    (root / "packages" / "yaap-core.json").write_text(json.dumps(record))
    if with_file:
        (root / "standards").mkdir()
        (root / "standards" / "working-method.md").write_bytes(WORKING)


@pytest.mark.parametrize(
    ("repository", "recorded", "with_file", "current", "state"),
    [
        ("jelvodev/yaap", "v0.5.6", True, "v0.5.6", (True, "v0.5.6", False)),
        ("jelvodev/yaap", "v0.5.6", True, "v0.5.7", (True, "v0.5.6", True)),
        ("jelvodev/yaap", "v0.5.6", True, "main", (True, "v0.5.6", True)),
        ("jelvodev/yaap", "main", True, "main", (True, "main", False)),
        ("JelvoDev/YAAP", "main", True, "main", (True, "main", False)),
        # Every item deleted: not installed any more.
        ("jelvodev/yaap", "v0.5.6", False, "v0.5.6", (False, None, False)),
        # A package of the same name from elsewhere is not the starter.
        ("someone/else", "v0.5.6", True, "v0.5.6", (False, None, False)),
    ],
)
def test_the_state_of_a_pack_in_a_layer(
    tmp_path: Path,
    repository: str,
    recorded: str,
    with_file: bool,
    current: str,
    state: tuple[bool, str | None, bool],
) -> None:
    write_record(tmp_path, repository, recorded, with_file)
    found = starter.pack_state(Layer("global", "Global", tmp_path, tmp_path), starter.CORE_PACK, current)
    assert (found.installed, found.installed_ref, found.outdated) == state
    assert found.url == starter.pack_url(starter.CORE_PACK, current)


def by_key(body: dict) -> dict[str, dict]:
    return {pack["key"]: pack for pack in body["packs"]}


def test_the_starter_per_level_and_moving_to_a_newer_tag(client, github, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(blueprint_starter, "VERSION", "0.5.6")
    body = client.get("/api/v1/blueprint/starter").json()
    assert body["ref"] == "v0.5.6" and body["pinned_to_version"] is True and body["version"] == "0.5.6"
    assert body["repository"] == "jelvodev/yaap"
    assert body["credits_url"] == "https://github.com/jelvodev/yaap/blob/v0.5.6/docs/credits.md"
    core = by_key(body)["core"]
    assert core["url"] == "https://github.com/jelvodev/yaap/tree/v0.5.6/blueprint/starter/core"
    assert core["chosen"] is True and core["installed"] is False and core["installed_ref"] is None
    assert {pack["installed"] for pack in body["packs"]} == {False}

    # Installing is the ordinary package flow with the pack's link.
    preview = client.post("/api/v1/blueprint/packages/preview", json={"url": core["url"]}).json()
    assert preview["source"]["ref"] == "v0.5.6" and preview["package"]["name"] == "yaap-core"
    items = [{"kind": item["kind"], "path": item["path"], "name": item["name"]} for item in preview["items"]]
    installed = client.post(
        "/api/v1/blueprint/packages/install",
        json={"url": core["url"], "ref": "v0.5.6", "commit": COMMIT_A, "items": items},
    )
    assert installed.status_code == 200, installed.text
    core = by_key(client.get("/api/v1/blueprint/starter").json())["core"]
    assert (core["installed"], core["installed_ref"], core["outdated"]) == (True, "v0.5.6", False)
    # Installed globally; an environment's own layer does not have it.
    environment = by_key(client.get("/api/v1/blueprint/starter", params={"environment_id": 1}).json())["core"]
    assert environment["installed"] is False

    # YAAP moves to 0.5.7, whose tag has a changed standard.
    github.add_commit(COMMIT_B, {f"{CORE}/standards/working-method.md": WORKING + b"\nSmall steps.\n"})
    github.refs["main"] = COMMIT_A
    github.refs["v0.5.7"] = COMMIT_B
    monkeypatch.setattr(blueprint_starter, "VERSION", "0.5.7")
    core = by_key(client.get("/api/v1/blueprint/starter").json())["core"]
    assert core["url"].endswith("/tree/v0.5.7/blueprint/starter/core")
    assert (core["installed"], core["installed_ref"], core["outdated"]) == (True, "v0.5.6", True)

    # Without a ref the update reads the recorded tag, which never moves.
    same = client.get("/api/v1/blueprint/packages/yaap-core/update").json()
    assert same["ref"] == "v0.5.6" and same["latest_commit"] == COMMIT_A
    assert {item["status"] for item in same["items"]} == {"up_to_date"}

    check = client.get("/api/v1/blueprint/packages/yaap-core/update", params={"ref": "v0.5.7"})
    assert check.status_code == 200, check.text
    check = check.json()
    assert check["ref"] == "v0.5.7" and check["latest_commit"] == COMMIT_B
    changed = {item["path"]: item for item in check["items"]}["standards/working-method.md"]
    assert changed["status"] == "changed" and "+Small steps." in changed["upstream_diff"]

    accepted = client.post(
        "/api/v1/blueprint/packages/yaap-core/update",
        json={"commit": COMMIT_B, "paths": ["standards/working-method.md"], "ref": "v0.5.7"},
    )
    assert accepted.status_code == 200, accepted.text
    root = Path(tmp_path) / "blueprint" / "global"
    record = json.loads((root / "packages" / "yaap-core.json").read_text())
    assert record["ref"] == "v0.5.7"
    assert (root / "standards" / "working-method.md").read_bytes().endswith(b"Small steps.\n")
    core = by_key(client.get("/api/v1/blueprint/starter").json())["core"]
    assert (core["installed_ref"], core["outdated"]) == ("v0.5.7", False)

    # The first start counts the core pack as installed.
    assert client.get("/api/v1/first-run").json()["steps"]["starter"] == "done"


def test_a_development_build_reads_main(client, github, monkeypatch) -> None:
    monkeypatch.setattr(blueprint_starter, "VERSION", "9.9.9")
    body = client.get("/api/v1/blueprint/starter").json()
    assert body["ref"] == "main" and body["pinned_to_version"] is False
    assert by_key(body)["writing"]["url"] == "https://github.com/jelvodev/yaap/tree/main/blueprint/starter/writing"


def test_an_unknown_ref_for_an_update_is_refused(client, github) -> None:
    core = starter.pack_url(starter.CORE_PACK, "v0.5.6")
    items = [{"kind": "standard", "path": "standards/working-method.md", "name": "working-method"}]
    response = client.post(
        "/api/v1/blueprint/packages/install", json={"url": core, "ref": "v0.5.6", "commit": COMMIT_A, "items": items}
    )
    assert response.status_code == 200, response.text
    refused = client.get("/api/v1/blueprint/packages/yaap-core/update", params={"ref": "v9.9.9"})
    assert refused.status_code == 422
