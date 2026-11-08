"""Blueprint packages from a fake GitHub."""

import json
import subprocess
from pathlib import Path

import pytest

from app.blueprint import packages, skill_install
from app.blueprint.packages import PackageError, parse_manifest
from app.blueprint.skill_install import API, RAW, SkillInstallError

COMMIT_A = "a" * 40
COMMIT_B = "b" * 40
REPOSITORY = "acme/blueprints"

MANIFEST = {
    "name": "acme-basics",
    "description": "General standards and skills.",
    "items": [
        {"kind": "standard", "path": "standards/security.md"},
        {"kind": "engineering", "path": "engineering/testing.md", "description": "How we test."},
        {"kind": "skill", "path": "skills/review-code"},
        {"kind": "command", "path": "commands/ship.md"},
        {"kind": "agent", "path": "agents/helper.md", "name": "acme-helper"},
        {"kind": "skill", "path": "skills/tools"},
    ],
}
SECURITY = b"---\nsummary: Keep secrets out of code.\nrules:\n  - Never commit secrets.\n---\n\nThe full standard.\n"
REVIEW = b"---\nname: review-code\ndescription: Review a change.\n---\n\nRead the diff first.\n"
FILES_A = {
    "README.md": b"# Acme Blueprint\n",
    "blueprint-package.json": json.dumps(MANIFEST).encode(),
    "standards/security.md": SECURITY,
    "engineering/testing.md": b"Test everything that can break.\r\n",
    "skills/review-code/SKILL.md": REVIEW,
    "skills/review-code/checklist.md": b"- Tests\n",
    "commands/ship.md": b"Ship it: run the checks, then open a pull request.\n",
    "agents/helper.md": b"---\ndescription: Helps.\n---\n\nYou help.\n",
    "skills/tools/SKILL.md": b"---\nname: tools\n---\n\nUse run.sh.\n",
    "skills/tools/run.sh": b"echo hi\n",
}


class FakeGitHub:
    """Serves acme/blueprints from in-memory files per commit."""

    def __init__(self) -> None:
        self.refs = {"main": COMMIT_A, "feature/x": COMMIT_A}
        self.files: dict[str, dict[str, bytes]] = {COMMIT_A: dict(FILES_A)}
        self.modes: dict[str, str] = {}
        self.calls: list[str] = []

    def add_commit(self, commit: str, changes: dict[str, bytes | None], ref: str = "main") -> None:
        files = dict(self.files[self.refs[ref]])
        for path, content in changes.items():
            if content is None:
                files.pop(path, None)
            else:
                files[path] = content
        self.files[commit] = files
        self.refs[ref] = commit

    def tree(self, commit: str, folder: str, recursive: bool) -> dict:
        prefix = f"{folder}/" if folder else ""
        entries: dict[str, dict] = {}
        for path, content in self.files[commit].items():
            if not path.startswith(prefix):
                continue
            relative = path[len(prefix) :]
            parts = relative.split("/")
            for depth in range(1, len(parts)):
                sub = "/".join(parts[:depth])
                if recursive or depth == 1:
                    entries[sub] = {"path": sub, "mode": "040000", "type": "tree", "sha": f"{commit}:{prefix}{sub}"}
            if recursive or len(parts) == 1:
                mode = self.modes.get(path, "100644")
                entries[relative] = {"path": relative, "mode": mode, "type": "blob", "sha": "f", "size": len(content)}
        return {"tree": list(entries.values()), "truncated": False}

    def __call__(self, url: str, token: str | None) -> bytes:
        self.calls.append(url)
        if url == f"{API}/repos/{REPOSITORY}":
            return json.dumps({"default_branch": "main"}).encode()
        commits = f"{API}/repos/{REPOSITORY}/commits/"
        if url.startswith(commits):
            ref = url[len(commits) :].replace("%2F", "/")
            sha = self.refs.get(ref) or (ref if ref in self.files else None)
            if sha is None:
                raise SkillInstallError("GitHub has nothing at this link.", 404)
            return json.dumps({"sha": sha, "commit": {"tree": {"sha": f"{sha}:"}}}).encode()
        trees = f"{API}/repos/{REPOSITORY}/git/trees/"
        if url.startswith(trees):
            sha, _, query = url[len(trees) :].partition("?")
            commit, _, folder = sha.partition(":")
            return json.dumps(self.tree(commit, folder, query == "recursive=1")).encode()
        raw = f"{RAW}/{REPOSITORY}/"
        if url.startswith(raw):
            commit, _, path = url[len(raw) :].partition("/")
            if path in self.files.get(commit, {}):
                return self.files[commit][path]
        raise SkillInstallError("GitHub has nothing at this link.", 404)


@pytest.fixture
def github(monkeypatch) -> FakeGitHub:
    fake = FakeGitHub()
    monkeypatch.setattr(skill_install, "http_get", fake)
    return fake


def commits(folder: Path) -> list[str]:
    output = subprocess.run(["git", "log", "--format=%s"], cwd=folder, capture_output=True, text=True, check=True)
    return output.stdout.splitlines()


def changed_files(folder: Path, revision: str = "HEAD") -> list[str]:
    output = subprocess.run(
        ["git", "show", "--name-only", "--format=", revision], cwd=folder, capture_output=True, text=True, check=True
    )
    return sorted(output.stdout.split())


URL = f"https://github.com/{REPOSITORY}"
CHOSEN = [
    {"kind": "standard", "path": "standards/security.md", "name": "security"},
    {"kind": "skill", "path": "skills/review-code", "name": "review-code"},
    {"kind": "command", "path": "commands/ship.md", "name": "ship"},
]


def install(client, items=CHOSEN, commit=COMMIT_A, url=URL, ref="main", params=None):
    return client.post(
        "/api/v1/blueprint/packages/install",
        params=params or {},
        json={"url": url, "ref": ref, "commit": commit, "items": items},
    )


def test_manifest_is_validated():
    manifest = parse_manifest(json.dumps(MANIFEST).encode())
    assert manifest.name == "acme-basics"
    assert [item.name for item in manifest.items] == ["security", "testing", "review-code", "ship", "acme-helper", "tools"]
    skill = parse_manifest(json.dumps({**MANIFEST, "items": [{"kind": "skill", "path": "a/b/SKILL.md"}]}).encode())
    assert skill.items[0].path == "a/b" and skill.items[0].name == "b"
    bad = [
        b"not json",
        json.dumps([1]).encode(),
        json.dumps({**MANIFEST, "name": "Acme Basics"}).encode(),
        json.dumps({**MANIFEST, "description": None}).encode(),
        json.dumps({**MANIFEST, "items": []}).encode(),
        json.dumps({**MANIFEST, "items": [{"kind": "instructions", "path": "instructions.md"}]}).encode(),
        json.dumps({**MANIFEST, "items": [{"kind": "standard", "path": "../secret.md"}]}).encode(),
        json.dumps({**MANIFEST, "items": [{"kind": "standard", "path": "run.py"}]}).encode(),
        json.dumps({**MANIFEST, "items": [{"kind": "command", "path": "a/x.md"}, {"kind": "command", "path": "b/x.md"}]}).encode(),
        json.dumps({**MANIFEST, "items": [{"kind": "standard", "path": "x.md", "name": "Bad Name"}]}).encode(),
    ]
    for data in bad:
        with pytest.raises(PackageError):
            parse_manifest(data)


def test_preview_shows_items_text_and_review(client, github):
    response = client.post("/api/v1/blueprint/packages/preview", json={"url": URL})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"]["commit"] == COMMIT_A and body["source"]["ref"] == "main"
    assert body["package"] == {"name": "acme-basics", "description": "General standards and skills."}
    items = {item["name"]: item for item in body["items"]}
    assert items["security"]["target"] == "standards/security.md"
    assert items["security"]["description"] == "Keep secrets out of code."
    assert "Never commit secrets." in items["security"]["text"]
    assert items["testing"]["description"] == "How we test." and items["testing"]["kind"] == "engineering"
    assert items["review-code"]["target"] == "skills/review-code/SKILL.md"
    assert [file["path"] for file in items["review-code"]["files"]] == ["SKILL.md", "checklist.md"]
    assert items["acme-helper"]["target"] == "agents/acme-helper.md"
    # A script in a package is refused, not only warned about.
    assert "run.sh" in items["tools"]["error"] and "text only" in items["tools"]["error"]
    assert all(item["error"] is None for name, item in items.items() if name != "tools")
    # Nothing is written by a preview.
    assert client.get("/api/v1/blueprint/items").json() == []


def test_links_to_a_folder_or_the_manifest(client, github):
    github.add_commit(
        COMMIT_B,
        {f"packs/basics/{path}": content for path, content in FILES_A.items() if path != "README.md"},
        ref="feature/x",
    )
    for url in (
        f"{URL}/tree/feature/x/packs/basics",
        f"{URL}/blob/feature/x/packs/basics/blueprint-package.json",
    ):
        body = client.post("/api/v1/blueprint/packages/preview", json={"url": url}).json()
        assert body["source"]["ref"] == "feature/x" and body["source"]["path"] == "packs/basics", body
        assert len(body["items"]) == 6
    response = install(client, items=CHOSEN[:1], commit=COMMIT_B, url=f"{URL}/tree/feature/x/packs/basics", ref="feature/x")
    assert response.status_code == 200, response.text
    package = client.get("/api/v1/blueprint/packages").json()[0]
    assert package["ref"] == "feature/x"
    origin = {item["path"]: item for item in client.get("/api/v1/blueprint/items").json()}["standards/security.md"]["package"]
    assert origin["commit_url"] == f"{URL}/blob/{COMMIT_B}/packs/basics/standards/security.md"


def test_a_link_without_a_manifest_is_refused(client, github):
    github.add_commit(COMMIT_B, {"blueprint-package.json": None})
    response = client.post("/api/v1/blueprint/packages/preview", json={"url": URL})
    assert response.status_code == 422 and "blueprint-package.json" in response.json()["detail"]
    response = client.post("/api/v1/blueprint/packages/preview", json={"url": f"{URL}/blob/main/README.md"})
    assert response.status_code == 422


def test_install_writes_chosen_items_pinned_to_the_commit(client, github, tmp_path):
    response = install(client)
    assert response.status_code == 200, response.text
    assert response.json() == ["standards/security.md", "skills/review-code/SKILL.md", "commands/ship.md"]
    root = Path(tmp_path) / "blueprint" / "global"
    assert (root / "standards" / "security.md").read_bytes() == SECURITY
    assert (root / "skills" / "review-code" / "checklist.md").read_bytes() == b"- Tests\n"
    assert not (root / "engineering").exists() and not (root / "skills" / "tools").exists()
    record = json.loads((root / "packages" / "acme-basics.json").read_text())
    assert record["repository"] == REPOSITORY and record["ref"] == "main" and record["url"] == URL
    assert {entry["commit"] for entry in record["items"].values()} == {COMMIT_A}
    assert record["items"]["skills/review-code/SKILL.md"]["source"] == "skills/review-code"
    source = json.loads((root / "skills" / "review-code" / "source.json").read_text())
    assert source["package"] == "acme-basics" and source["commit"] == COMMIT_A
    # One commit for the items and the record.
    assert commits(root) == [f"Install security, review-code, ship from package acme-basics ({REPOSITORY}@aaaaaaa, Global)"]
    assert changed_files(root) == [
        "commands/ship.md",
        "packages/acme-basics.json",
        "skills/review-code/SKILL.md",
        "skills/review-code/checklist.md",
        "skills/review-code/source.json",
        "standards/security.md",
    ]

    items = {item["path"]: item for item in client.get("/api/v1/blueprint/items").json()}
    origin = items["standards/security.md"]["package"]
    assert origin["name"] == "acme-basics" and origin["commit"] == COMMIT_A and origin["changed_locally"] is False
    assert origin["commit_url"] == f"{URL}/blob/{COMMIT_A}/standards/security.md"
    skill = items["skills/review-code/SKILL.md"]
    assert skill["package"]["commit_url"] == f"{URL}/tree/{COMMIT_A}/skills/review-code"
    assert skill["origin"] is None  # Shown and updated through its package, not as a skill from GitHub.

    listed = client.get("/api/v1/blueprint/packages").json()
    assert [package["name"] for package in listed] == ["acme-basics"]
    assert [item["path"] for item in listed[0]["items"]] == [
        "commands/ship.md", "skills/review-code/SKILL.md", "standards/security.md",
    ]

    # An edit marks the item as changed locally; deleting it drops it from the package.
    client.put("/api/v1/blueprint/file", params={"path": "commands/ship.md"}, json={"content": "Mine.\n"})
    items = {item["path"]: item for item in client.get("/api/v1/blueprint/items").json()}
    assert items["commands/ship.md"]["package"]["changed_locally"] is True
    assert client.delete("/api/v1/blueprint/file", params={"path": "commands/ship.md"}).status_code == 204
    record = json.loads((root / "packages" / "acme-basics.json").read_text())
    assert "commands/ship.md" not in record["items"]


def test_install_renames_and_into_the_chosen_level(client, github, tmp_path):
    environment = next(item for item in client.get("/api/v1/environments").json() if item["name"] == "Work")
    params = {"environment_id": environment["id"]}
    response = install(client, items=[{"kind": "agent", "path": "agents/helper.md", "name": "aide"}], params=params)
    assert response.status_code == 200, response.text
    assert response.json() == ["agents/aide.md"]
    written = list((Path(tmp_path) / "blueprint").glob("*/agents/aide.md"))
    assert len(written) == 1 and written[0].parent.parent.name != "global"
    assert not (Path(tmp_path) / "blueprint" / "global" / "agents").exists()
    assert client.get("/api/v1/blueprint/packages").json() == []
    assert [package["name"] for package in client.get("/api/v1/blueprint/packages", params=params).json()] == ["acme-basics"]
    # A second preview at that level says the item is there already.
    preview = client.post("/api/v1/blueprint/packages/preview", params=params, json={"url": URL}).json()
    helper = next(item for item in preview["items"] if item["path"] == "agents/helper.md")
    assert helper["exists"] is False  # Renamed on install, so the manifest's name is still free.


def test_scripts_and_binaries_in_a_package_are_refused(client, github, tmp_path):
    root = Path(tmp_path) / "blueprint" / "global"
    refused = install(client, items=[*CHOSEN, {"kind": "skill", "path": "skills/tools", "name": "tools"}])
    assert refused.status_code == 422 and "run.sh" in refused.json()["detail"]
    assert not root.exists() or not (root / "standards").exists()

    # An executable bit, binary content or a shebang is refused even without a script suffix.
    github.add_commit(COMMIT_B, {"skills/review-code/helper": b"#!/bin/sh\necho hi\n"})
    response = install(client, items=CHOSEN[1:2], commit=COMMIT_B)
    assert response.status_code == 422 and "script helper" in response.json()["detail"]
    github.modes["skills/review-code/checklist.md"] = "100755"
    preview = client.post("/api/v1/blueprint/packages/preview", json={"url": URL}).json()
    review = next(item for item in preview["items"] if item["name"] == "review-code")
    assert "checklist.md" in review["error"]
    github.modes.clear()
    github.add_commit("c" * 40, {"commands/ship.md": b"text\0with a zero byte\n"})
    response = install(client, items=CHOSEN[2:], commit="c" * 40)
    assert response.status_code == 422 and "binary" in response.json()["detail"]
    assert not (root / "commands").exists() and not (root / "skills").exists()


def test_install_refuses_unknown_items_and_a_name_taken_by_another_repository(client, github, tmp_path):
    missing = install(client, items=[{"kind": "standard", "path": "standards/nope.md", "name": "nope"}])
    assert missing.status_code == 422
    wrong_kind = install(client, items=[{"kind": "command", "path": "standards/security.md", "name": "security"}])
    assert wrong_kind.status_code == 422
    bad_name = install(client, items=[{"kind": "standard", "path": "standards/security.md", "name": "Bad"}])
    assert bad_name.status_code == 422
    root = Path(tmp_path) / "blueprint" / "global"
    record = {"name": "acme-basics", "url": "https://github.com/other/repo", "repository": "other/repo", "ref": "main",
              "path": "", "items": {}}
    (root / "packages").mkdir(parents=True)
    (root / "packages" / "acme-basics.json").write_text(json.dumps(record))
    taken = install(client)
    assert taken.status_code == 422 and "other/repo" in taken.json()["detail"]
    assert packages.read_record(packages.Layer("global", "Global", root, root), "acme-basics")["items"] == {}


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        file.relative_to(root).as_posix(): file.read_bytes()
        for file in sorted(root.rglob("*"))
        if file.is_file() and ".git" not in file.relative_to(root).parts
    }


def test_an_update_shows_a_diff_per_item_and_changes_nothing_until_accepted(client, github, tmp_path):
    root = Path(tmp_path) / "blueprint" / "global"
    assert install(client).status_code == 200
    check = client.get("/api/v1/blueprint/packages/acme-basics/update").json()
    assert check["latest_commit"] == COMMIT_A and check["new_items"] == []
    assert {item["status"] for item in check["items"]} == {"up_to_date"}

    manifest = {**MANIFEST, "items": [*MANIFEST["items"], {"kind": "standard", "path": "standards/privacy.md"}]}
    github.add_commit(
        COMMIT_B,
        {
            "blueprint-package.json": json.dumps(manifest).encode(),
            "standards/security.md": SECURITY + b"\nRotate keys yearly.\n",
            "skills/review-code/checklist.md": b"- Tests\n- Docs\n",
            "standards/privacy.md": b"---\nsummary: Keep personal data minimal.\n---\n\nText.\n",
        },
    )
    client.put("/api/v1/blueprint/file", params={"path": "commands/ship.md"}, json={"content": "My own release steps.\n"})
    before, history = snapshot(root), commits(root)

    check = client.get("/api/v1/blueprint/packages/acme-basics/update").json()
    assert check["latest_commit"] == COMMIT_B and check["ref"] == "main"
    items = {item["path"]: item for item in check["items"]}
    security = items["standards/security.md"]
    assert security["status"] == "changed" and security["installed_commit"] == COMMIT_A
    assert "+Rotate keys yearly." in security["upstream_diff"] and "Rotate keys yearly." in security["item"]["text"]
    review = items["skills/review-code/SKILL.md"]
    assert review["status"] == "changed" and "+- Docs" in review["upstream_diff"]
    assert "skills/review-code/checklist.md" in review["upstream_diff"]
    ship = items["commands/ship.md"]
    assert ship["status"] == "up_to_date" and ship["item"] is None
    assert ship["changed_locally"] is True and "+My own release steps." in ship["local_diff"]
    assert [item["name"] for item in check["new_items"]] == ["privacy"]
    # Checking changed nothing.
    assert snapshot(root) == before and commits(root) == history

    # Accept one item: only that one is written, pinned to the new commit, as one commit.
    accepted = client.post(
        "/api/v1/blueprint/packages/acme-basics/update",
        json={"commit": COMMIT_B, "paths": ["standards/security.md"]},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json() == ["standards/security.md"]
    assert (root / "standards" / "security.md").read_bytes().endswith(b"Rotate keys yearly.\n")
    assert (root / "skills" / "review-code" / "checklist.md").read_bytes() == b"- Tests\n"
    assert (root / "commands" / "ship.md").read_text() == "My own release steps.\n"
    assert not (root / "standards" / "privacy.md").exists()
    record = json.loads((root / "packages" / "acme-basics.json").read_text())
    assert record["items"]["standards/security.md"]["commit"] == COMMIT_B
    assert record["items"]["skills/review-code/SKILL.md"]["commit"] == COMMIT_A
    assert commits(root)[0] == f"Update security from package acme-basics ({REPOSITORY}@bbbbbbb, Global)"
    assert changed_files(root) == ["packages/acme-basics.json", "standards/security.md"]

    check = client.get("/api/v1/blueprint/packages/acme-basics/update").json()
    items = {item["path"]: item for item in check["items"]}
    assert items["standards/security.md"]["status"] == "up_to_date"
    assert items["skills/review-code/SKILL.md"]["status"] == "changed"


def test_removed_items_and_scripts_in_an_update(client, github, tmp_path):
    root = Path(tmp_path) / "blueprint" / "global"
    assert install(client).status_code == 200
    manifest = {**MANIFEST, "items": [item for item in MANIFEST["items"] if item["path"] != "commands/ship.md"]}
    github.add_commit(
        COMMIT_B,
        {"blueprint-package.json": json.dumps(manifest).encode(), "skills/review-code/setup.ps1": b"Write-Host hi\n"},
    )
    check = client.get("/api/v1/blueprint/packages/acme-basics/update").json()
    items = {item["path"]: item for item in check["items"]}
    assert items["commands/ship.md"]["status"] == "removed"
    review = items["skills/review-code/SKILL.md"]
    assert review["status"] == "changed" and "setup.ps1" in review["item"]["error"]
    history = commits(root)
    for paths in (["skills/review-code/SKILL.md"], ["commands/ship.md"], ["standards/unknown.md"]):
        refused = client.post("/api/v1/blueprint/packages/acme-basics/update", json={"commit": COMMIT_B, "paths": paths})
        assert refused.status_code == 422, paths
    assert commits(root) == history
    assert not (root / "skills" / "review-code" / "setup.ps1").exists()
    assert client.get("/api/v1/blueprint/packages/nope/update").status_code == 404
