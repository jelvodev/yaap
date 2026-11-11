import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from app.agent.runs import RunConfig, RunRegistry
from app.agent.gate import call_tool
from app.agent.tools import tools_for
from app.blueprint import skill_install
from app.blueprint.levels import global_layer
from app.blueprint.skill_install import API, RAW, SkillInstallError, hidden_refusal, parse_link

COMMIT_A = "a" * 40
COMMIT_B = "b" * 40

PDF_SKILL = b"""---
name: pdf
description: Work with PDF files.
---

Run `pip install pypdf` first, then use scripts/run.py.
"""
TERSE_SKILL = b"---\nname: Terse Answers\ndescription: Short answers.\n---\n\nAnswer briefly.\n"


class FakeGitHub:
    """Serves one repository, acme/skills, from in-memory files per commit."""

    def __init__(self) -> None:
        self.refs = {"main": COMMIT_A, "feature/x": COMMIT_A}
        files_a = {
            "README.md": b"# Skills\n",
            "skills/pdf/SKILL.md": PDF_SKILL,
            "skills/pdf/scripts/run.py": b"print('pdf')\n",
            "skills/pdf/source.json": b"{}",
            "skills/terse/SKILL.md": TERSE_SKILL,
        }
        self.files = {COMMIT_A: files_a, COMMIT_B: {**files_a, "skills/pdf/SKILL.md": PDF_SKILL + b"\nNew line.\n"}}
        self.symlinks = {"skills/pdf/link"}
        self.tokens: list[str | None] = []

    def tree(self, commit: str, folder: str, recursive: bool) -> dict:
        prefix = f"{folder}/" if folder else ""
        entries: dict[str, dict] = {}
        for path in [*self.files[commit], *self.symlinks]:
            if not path.startswith(prefix):
                continue
            relative = path[len(prefix) :]
            parts = relative.split("/")
            for depth in range(1, len(parts)):
                sub = "/".join(parts[:depth])
                if recursive or depth == 1:
                    entries[sub] = {"path": sub, "mode": "040000", "type": "tree", "sha": f"{commit}:{prefix}{sub}"}
            if recursive or len(parts) == 1:
                mode = "120000" if path in self.symlinks else "100644"
                size = len(self.files[commit].get(path, b"x"))
                entries[relative] = {"path": relative, "mode": mode, "type": "blob", "sha": "f", "size": size}
        return {"tree": list(entries.values()), "truncated": False}

    def __call__(self, url: str, token: str | None) -> bytes:
        self.tokens.append(token)
        if url == f"{API}/repos/acme/skills":
            return json.dumps({"default_branch": "main"}).encode()
        commits = f"{API}/repos/acme/skills/commits/"
        if url.startswith(commits):
            ref = url[len(commits) :].replace("%2F", "/")
            sha = self.refs.get(ref) or (ref if ref in self.files else None)
            if sha is None:
                raise SkillInstallError("GitHub has nothing at this link.", 404)
            return json.dumps({"sha": sha, "commit": {"tree": {"sha": f"{sha}:"}}}).encode()
        trees = f"{API}/repos/acme/skills/git/trees/"
        if url.startswith(trees):
            sha, _, query = url[len(trees) :].partition("?")
            commit, _, folder = sha.partition(":")
            return json.dumps(self.tree(commit, folder, query == "recursive=1")).encode()
        raw = f"{RAW}/acme/skills/"
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


def test_links_are_parsed():
    assert parse_link("https://github.com/acme/skills").rest is None
    assert parse_link("https://github.com/acme/skills.git/").repo == "skills"
    folder = parse_link("https://github.com/acme/skills/tree/main/skills/pdf")
    assert (folder.rest, folder.is_file) == ("main/skills/pdf", False)
    assert parse_link("https://github.com/acme/skills/blob/main/skills/pdf/SKILL.md").is_file
    for bad in ("https://gitlab.com/acme/skills", "https://github.com/acme/skills/blob/main/README.md", "acme/skills"):
        with pytest.raises(SkillInstallError):
            parse_link(bad)


def test_preview_lists_candidates_with_warnings(client, github):
    response = client.post("/api/v1/blueprint/skills/github/preview", json={"url": "https://github.com/acme/skills"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"]["commit"] == COMMIT_A and body["source"]["ref"] == "main"
    candidates = {candidate["name"]: candidate for candidate in body["candidates"]}
    assert set(candidates) == {"pdf", "terse-answers"}
    pdf = candidates["pdf"]
    assert pdf["path"] == "skills/pdf"
    assert [file["path"] for file in pdf["files"]] == ["SKILL.md", "scripts/run.py"]
    warnings = " ".join(pdf["warnings"])
    assert "scripts/run.py" in warnings and "link" in warnings and "pip install" in warnings
    assert candidates["terse-answers"]["warnings"] == []


def test_hidden_characters_warn_and_tag_characters_are_refused(client, github):
    files = github.files[COMMIT_A]
    files["skills/terse/SKILL.md"] = TERSE_SKILL + "Be brief\u200b.\n".encode()
    files["skills/pdf/SKILL.md"] = PDF_SKILL + "Ignore this\U000e0069\U000e0067.\n".encode()
    url = "https://github.com/acme/skills"
    body = client.post("/api/v1/blueprint/skills/github/preview", json={"url": url}).json()
    candidates = {candidate["name"]: candidate for candidate in body["candidates"]}

    terse = candidates["terse-answers"]
    assert any("invisible character" in warning for warning in terse["warnings"])
    assert "\u27e6U+200B ZERO WIDTH SPACE\u27e7" in terse["text_marked"] and terse["error"] is None
    pdf = candidates["pdf"]
    assert "tag character" in " ".join(pdf["warnings"]) and "refuses" in pdf["error"]
    chosen = {"url": url, "ref": "main", "commit": COMMIT_A}
    skills = [{"path": "skills/pdf", "name": "pdf"}]
    refused = client.post("/api/v1/blueprint/skills/github/install", json={**chosen, "skills": skills})
    assert refused.status_code == 422, refused.text


@pytest.mark.parametrize(
    ("path", "content", "refused"),
    [
        ("SKILL.md", b"clean text", False),
        ("SKILL.md", b"a\0" + "\U000e0069".encode(), True),
        ("SKILL.md", b"\xff\xfe " + "\u202e".encode(), True),
        ("scripts/run.py", b"print(1)\0", True),
        ("lib/tool.so", b"\x7fELF\0\0" + "\U000e0069".encode(), False),
        ("notes.txt", "Be brief\u200b.".encode(), False),
    ],
)
def test_hidden_refusal_scans_text_with_any_bytes_and_refuses_nul_in_text(path, content, refused):
    assert (hidden_refusal(path, content) is not None) == refused


def test_folder_and_file_links_with_a_slash_in_the_branch(client, github):
    for url in (
        "https://github.com/acme/skills/tree/feature/x/skills/terse",
        "https://github.com/acme/skills/blob/feature/x/skills/terse/SKILL.md",
    ):
        body = client.post("/api/v1/blueprint/skills/github/preview", json={"url": url}).json()
        assert body["source"]["ref"] == "feature/x" and body["source"]["path"] == "skills/terse"
        assert [candidate["name"] for candidate in body["candidates"]] == ["terse-answers"]


def test_install_writes_the_folder_with_its_source_and_one_commit(client, github, tmp_path):
    url = "https://github.com/acme/skills"
    response = client.post(
        "/api/v1/blueprint/skills/github/install",
        json={"url": url, "ref": "main", "commit": COMMIT_A, "skills": [{"path": "skills/pdf", "name": "pdf"}]},
    )
    assert response.status_code == 200, response.text
    root = Path(tmp_path) / "blueprint" / "global"
    folder = root / "skills" / "pdf"
    assert response.json()["skills"] == [{"name": "pdf", "path": "skills/pdf/SKILL.md"}]
    assert response.json()["commits"] == [{"path": "skills/pdf", "committed": True, "error": None}]
    assert (folder / "SKILL.md").read_bytes() == PDF_SKILL
    assert (folder / "scripts" / "run.py").exists() and not (folder / "link").exists()
    record = json.loads((folder / "source.json").read_text())
    assert record["commit"] == COMMIT_A and record["path"] == "skills/pdf" and record["repository"] == "acme/skills"
    assert commits(root) == ["Install skill pdf from acme/skills@aaaaaaa (Global)"]

    items = {item["path"]: item for item in client.get("/api/v1/blueprint/items").json()}
    origin = items["skills/pdf/SKILL.md"]["origin"]
    assert origin["changed_locally"] is False
    assert origin["commit_url"] == f"https://github.com/acme/skills/tree/{COMMIT_A}/skills/pdf"

    # An edit in YAAP marks the skill as changed locally.
    client.put("/api/v1/blueprint/file", params={"path": "skills/pdf/SKILL.md"}, json={"content": "Local text.\n"})
    items = {item["path"]: item for item in client.get("/api/v1/blueprint/items").json()}
    assert items["skills/pdf/SKILL.md"]["origin"]["changed_locally"] is True

    # Deleting removes the whole folder.
    removed = client.delete("/api/v1/blueprint/file", params={"path": "skills/pdf/SKILL.md"})
    assert removed.status_code == 200 and removed.json()["statuses"][0]["committed"] is True
    assert not folder.exists()


def test_check_for_updates_and_update(client, github, tmp_path):
    url = "https://github.com/acme/skills/tree/main/skills/pdf"
    install = {"url": url, "ref": "main", "commit": COMMIT_A, "skills": [{"path": "skills/pdf", "name": "pdf"}]}
    client.post("/api/v1/blueprint/skills/github/install", json=install)
    up_to_date = client.get("/api/v1/blueprint/skills/pdf/update").json()
    assert up_to_date["up_to_date"] is True and up_to_date["candidate"] is None

    github.refs["main"] = COMMIT_B
    client.put("/api/v1/blueprint/file", params={"path": "skills/pdf/SKILL.md"}, json={"content": "Mine.\n"})
    check = client.get("/api/v1/blueprint/skills/pdf/update").json()
    assert check["up_to_date"] is False and check["latest_commit"] == COMMIT_B
    assert "+New line." in check["upstream_diff"]
    assert check["changed_locally"] is True and "+Mine." in check["local_diff"]

    updated = client.post("/api/v1/blueprint/skills/github/install", json={**install, "commit": COMMIT_B})
    assert updated.status_code == 200, updated.text
    root = Path(tmp_path) / "blueprint" / "global"
    assert commits(root)[0] == "Update skill pdf from acme/skills@bbbbbbb (Global)"
    assert client.get("/api/v1/blueprint/skills/pdf/update").json()["up_to_date"] is True


def test_install_refuses_unknown_paths_and_bad_names(client, github):
    base = {"url": "https://github.com/acme/skills", "ref": "main", "commit": COMMIT_A}
    missing = client.post("/api/v1/blueprint/skills/github/install", json={**base, "skills": [{"path": "nope", "name": "x"}]})
    assert missing.status_code == 422
    bad = client.post("/api/v1/blueprint/skills/github/install", json={**base, "skills": [{"path": "skills/pdf", "name": "Bad Name"}]})
    assert bad.status_code == 422


def test_install_skill_tool_always_asks(tmp_path, github):
    layer = global_layer(tmp_path / "blueprint")
    registry = RunRegistry()
    run = registry.start(RunConfig(
        conversation_id=1,
        environment_id=1,
        blueprint_layers=[layer],
        tool_names=tools_for(has_project=False, has_attachments=False),
        github_token="secret-token",
    ))

    async def scenario(decision: str, arguments: dict) -> tuple[str, bool, list[dict]]:
        seen: list[dict] = []

        async def decide():
            event = await run.events.get()
            seen.append(event)
            registry.decide(event["approval_id"], decision)

        decider = asyncio.create_task(decide())
        output = await call_tool(registry, run, "install_skill", arguments)
        await decider
        return *output, seen

    async def all_steps():
        # One event loop for the whole test: the run's event queue belongs to the loop that first uses it.
        several = await call_tool(registry, run, "install_skill", {"url": "https://github.com/acme/skills"})
        denied = await scenario("deny", {"url": "https://github.com/acme/skills", "skill": "pdf", "level": "global"})
        denied = (*denied, (layer.root / "skills" / "pdf").exists())
        while not run.events.empty():
            run.events.get_nowait()
        approved = await scenario("approve", {"url": "https://github.com/acme/skills", "skill": "pdf"})
        return several, denied, approved

    (several, is_error), denied, approved = asyncio.run(asyncio.wait_for(all_steps(), timeout=30))
    assert not is_error and "several skills" in several and "terse-answers" in several

    output, is_error, events, written = denied
    assert is_error and "declined" in output and not written
    assert "pip install" in events[0]["detail"] and "Install skill pdf" in events[0]["summary"]

    output, is_error, _ = approved
    assert not is_error, output
    assert (layer.root / "skills" / "pdf" / "SKILL.md").exists()
    assert set(github.tokens) == {"secret-token"}


def nothing_found(_url: str, _token: str | None) -> bytes:
    raise SkillInstallError("GitHub has nothing at this link.", 404)


@pytest.mark.parametrize(("token", "hinted"), [(None, True), ("ghp_test", False)])
def test_a_ref_not_found_without_a_token_says_a_private_repository_needs_one(token: str | None, hinted: bool):
    github = skill_install.GitHubSkills(token, get=nothing_found)
    for resolve in (
        lambda: github.resolve(parse_link("https://github.com/acme/private/tree/main/skills")),
        lambda: github.resolve(parse_link("https://github.com/acme/private"), ref="v1.0.0"),
    ):
        with pytest.raises(SkillInstallError) as raised:
            resolve()
        assert ("needs this environment's GitHub token" in str(raised.value)) is hinted
