"""GitHub work: token check, repositories and templates, branches and pull requests, against a fake
GitHub behind httpx.MockTransport."""

import asyncio
import json
import subprocess
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from app.agent.policy import ToolPolicy
from app.agent.runs import RunConfig, RunRegistry
from app.agent.gate import call_tool
from app.agent.tools import tools_for
from app.github import client as github_client
from app.vault.store import save_entry
from tests.test_tool_guard import answer_approvals

TOKEN = "ghp_" + "a" * 36


def git(*arguments: str, cwd: Path) -> str:
    return subprocess.run(["git", *arguments], cwd=cwd, capture_output=True, text=True, check=True).stdout


class FakeGitHub:
    """Just enough of GitHub's REST API, recording every request."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict]] = []
        self.scopes: str | None = "repo, workflow"
        self.repos = {
            "me/site": {"full_name": "me/site", "private": True, "default_branch": "main", "is_template": False},
            "me/starter": {"full_name": "me/starter", "private": False, "default_branch": "main", "is_template": True},
        }
        self.pulls: dict[int, dict] = {}
        self.deleted_branches: list[str] = []
        self.refuse_pulls = False

    def repo(self, name: str) -> dict:
        item = self.repos[name]
        return {
            **item,
            "html_url": f"https://github.com/{name}",
            "clone_url": f"https://github.com/{name}.git",
            "description": "",
            "updated_at": "2026-11-07T10:00:00Z",
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        path, method = request.url.path, request.method
        self.requests.append((method, path, body))
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        if path == "/user":
            headers = {"X-OAuth-Scopes": self.scopes} if self.scopes is not None else {}
            return httpx.Response(200, json={"login": "me"}, headers=headers)
        if path == "/user/repos" and method == "GET":
            return httpx.Response(200, json=[self.repo(name) for name in self.repos])
        if path == "/user/repos" and method == "POST":
            name = f"me/{body['name']}"
            self.repos[name] = {"full_name": name, "private": body["private"], "default_branch": "main", "is_template": False}
            return httpx.Response(201, json=self.repo(name))
        if path.endswith("/generate") and method == "POST":
            name = f"me/{body['name']}"
            self.repos[name] = {"full_name": name, "private": body["private"], "default_branch": "main", "is_template": False}
            return httpx.Response(201, json=self.repo(name))
        parts = path.strip("/").split("/")
        if parts[0] == "repos":
            name = f"{parts[1]}/{parts[2]}"
            rest = parts[3:]
            if not rest and method == "GET":
                return httpx.Response(200, json=self.repo(name))
            if not rest and method == "PATCH":
                self.repos[name]["is_template"] = body["is_template"]
                return httpx.Response(200, json=self.repo(name))
            if rest == ["pulls"] and method == "POST":
                if self.refuse_pulls:
                    return httpx.Response(
                        403,
                        json={"message": "Resource not accessible by personal access token"},
                        headers={"X-Accepted-GitHub-Permissions": "pull_requests=write"},
                    )
                number = len(self.pulls) + 1
                self.pulls[number] = {
                    "number": number,
                    "title": body["title"],
                    "body": body["body"],
                    "state": "open",
                    "draft": body["draft"],
                    "user": {"login": "me"},
                    "head": {"ref": body["head"], "sha": "abc123"},
                    "base": {"ref": body["base"]},
                    "html_url": f"https://github.com/{name}/pull/{number}",
                    "mergeable": True,
                }
                return httpx.Response(201, json=self.pulls[number])
            if rest == ["pulls"] and method == "GET":
                return httpx.Response(200, json=list(self.pulls.values()))
            if len(rest) == 2 and rest[0] == "pulls":
                return httpx.Response(200, json=self.pulls[int(rest[1])])
            if len(rest) == 3 and rest[2] == "files":
                return httpx.Response(
                    200, json=[{"filename": "index.html", "status": "modified", "additions": 1, "deletions": 1, "patch": "@@ -1 +1 @@\n-a\n+b"}]
                )
            if len(rest) == 3 and rest[2] == "merge":
                self.pulls[int(rest[1])]["merged"] = True
                return httpx.Response(200, json={"sha": "merged123", "merged": True})
            if rest == ["commits"]:
                return httpx.Response(200, json=[{"sha": "first"}])
            if rest[:2] == ["commits", "abc123"]:
                return httpx.Response(200, json={"check_runs": [{"name": "tests", "status": "completed", "conclusion": "success"}]})
            if rest[:3] == ["git", "refs", "heads"] and method == "DELETE":
                self.deleted_branches.append("/".join(rest[3:]))
                return httpx.Response(204)
        return httpx.Response(404, json={"message": "Not Found"})


@pytest.fixture
def github(monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setattr(github_client, "DEFAULT_TRANSPORT", httpx.MockTransport(fake.handle))
    return fake


def store_token(client):
    with client.app.state.session_factory() as session:
        save_entry(
            session,
            client.app.state.cipher,
            environment_id=1,
            project_id=None,
            name="default",
            kind="github_token",
            value=TOKEN,
            agent_use=False,
        )
        session.commit()


def test_the_token_check_names_missing_scopes(client, github):
    assert client.get("/api/v1/environments/1/github/token").json()["kind"] == "none"
    store_token(client)
    checked = client.get("/api/v1/environments/1/github/token").json()
    assert checked["kind"] == "classic" and checked["login"] == "me" and checked["missing"] == []
    github.scopes = "read:user"
    assert client.get("/api/v1/environments/1/github/token").json()["missing"] == ["repo"]
    github.scopes = None
    fine = client.get("/api/v1/environments/1/github/token").json()
    assert fine["kind"] == "fine_grained" and "Pull requests: write" in fine["message"]


def test_repositories_list_their_projects_and_can_be_templates(client, github):
    store_token(client)
    project = client.post("/api/v1/environments/1/projects", json={"name": "Site"}).json()
    client.put(
        f"/api/v1/environments/1/git/repositories/project-{project['id']}/remote",
        json={"url": "https://github.com/me/site.git"},
    )
    listed = {item["full_name"]: item for item in client.get("/api/v1/environments/1/github/repositories").json()}
    assert listed["me/site"]["projects"] == ["Site"] and listed["me/starter"]["is_template"] is True

    marked = client.patch("/api/v1/environments/1/github/repositories/me/site", json={"is_template": True})
    assert marked.status_code == 200 and marked.json()["is_template"] is True


def test_a_new_repository_is_empty_or_from_a_template(client, github):
    store_token(client)
    empty = client.post("/api/v1/environments/1/github/repositories", json={"name": "notes", "private": True})
    assert empty.status_code == 201 and empty.json()["repository"]["full_name"] == "me/notes"
    assert ("POST", "/user/repos", {"name": "notes", "private": True, "description": "", "auto_init": True}) in github.requests

    from_template = client.post(
        "/api/v1/environments/1/github/repositories", json={"name": "shop", "template": "me/starter"}
    )
    assert from_template.status_code == 201
    assert any(path == "/repos/me/starter/generate" for _, path, _ in github.requests)
    assert client.post("/api/v1/environments/1/github/repositories", json={"name": "bad name"}).status_code == 422


@pytest.fixture
def project_repo(client, tmp_path):
    """A project with a commit on main, pushed to a local bare repository, whose remote then points at GitHub."""
    project = client.post("/api/v1/environments/1/projects", json={"name": "Site"}).json()
    folder = Path(tmp_path) / "workspace" / project["folder"]
    remote = tmp_path / "remote.git"
    git("init", "--bare", "-b", "main", str(remote), cwd=tmp_path)
    (folder / "index.html").write_text("a\n")
    git("add", "-A", cwd=folder)
    git("-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-m", "Start", cwd=folder)
    git("remote", "add", "origin", str(remote), cwd=folder)
    git("push", "-u", "origin", "HEAD", cwd=folder)
    return project, folder, remote


def point_at_github(folder: Path) -> None:
    git("remote", "set-url", "origin", "https://github.com/me/site.git", cwd=folder)


def test_branches_are_created_switched_and_deleted(client, project_repo):
    project, folder, _ = project_repo
    base = f"/api/v1/environments/1/git/repositories/project-{project['id']}/branches"
    created = client.post(base, json={"name": "feature/login"}).json()
    assert [b["name"] for b in created["branches"] if b["current"]] == ["feature/login"]
    assert client.post(base, json={"name": "bad..name"}).status_code == 409

    refused = client.post(f"{base}/delete", json={"name": "feature/login"})
    assert refused.status_code == 409 and "current branch" in refused.json()["detail"]
    (folder / "index.html").write_text("b\n")
    git("-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-am", "Change", cwd=folder)
    switched = client.post(f"{base}/switch", json={"name": "main"}).json()
    assert switched["repository"]["branch"] == "main"
    unmerged = client.post(f"{base}/delete", json={"name": "feature/login"})
    assert unmerged.status_code == 409 and "not merged" in unmerged.json()["detail"]
    deleted = client.post(f"{base}/delete", json={"name": "feature/login", "force": True}).json()
    assert [b["name"] for b in deleted["branches"]] == ["main"]


def test_a_pull_request_is_created_opened_and_merged(client, github, project_repo, monkeypatch):
    store_token(client)
    project, folder, _ = project_repo
    git("switch", "-c", "feature", cwd=folder)
    (folder / "index.html").write_text("b\n")
    git("-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-am", "Change the page", cwd=folder)
    point_at_github(folder)
    pushed: list[Path] = []
    monkeypatch.setattr("app.api.github.push", lambda path, token: pushed.append(path))
    fetched: list[Path] = []
    monkeypatch.setattr("app.api.github.fetch", lambda path, token: fetched.append(path))
    base = f"/api/v1/environments/1/git/repositories/project-{project['id']}/pulls"

    created = client.post(base, json={"title": "Change the page", "body": "Why"})
    assert created.status_code == 201, created.text
    assert created.json()["head"] == "feature" and created.json()["base"] == "main"
    assert pushed == [folder]  # The branch had no upstream, so it was pushed first.

    listed = client.get(base).json()
    assert listed["repository"] == "me/site" and listed["current_branch"] == "feature"
    assert [pull["number"] for pull in listed["pulls"]] == [1]
    detail = client.get(f"{base}/1").json()
    assert detail["files"][0]["path"] == "index.html" and detail["checks"][0]["conclusion"] == "success"

    merged = client.post(f"{base}/1/merge", json={"delete_branch": True}).json()
    assert merged == {"sha": "merged123", "branch_deleted": True}
    assert ("PUT", "/repos/me/site/pulls/1/merge", {"merge_method": "squash"}) in github.requests
    assert github.deleted_branches == ["feature"]
    assert fetched == [folder]  # YAAP fetches after merging, so the panel shows the merge.


def test_a_refusal_names_the_permission_the_token_lacks(client, github, project_repo, monkeypatch):
    store_token(client)
    project, folder, _ = project_repo
    git("switch", "-c", "feature", cwd=folder)
    point_at_github(folder)
    monkeypatch.setattr("app.api.github.push", lambda path, token: None)
    github.refuse_pulls = True
    refused = client.post(
        f"/api/v1/environments/1/git/repositories/project-{project['id']}/pulls", json={"title": "T"}
    )
    assert refused.status_code == 409 and "pull_requests=write" in refused.json()["detail"]


def test_the_agent_opens_a_pull_request_only_after_approval_and_a_push(github, project_repo):
    _, folder, _ = project_repo
    git("switch", "-c", "feature", cwd=folder)
    point_at_github(folder)
    registry = RunRegistry()
    run = registry.start(RunConfig(
        conversation_id=1,
        environment_id=1,
        project_id=1,
        project_folder=folder,
        blueprint_layers=[],
        tool_names=tools_for(has_project=True, has_attachments=False),
        github_token=TOKEN,
        policy=ToolPolicy(),
    ))
    seen: list[dict] = []

    async def scenario(decision):
        run.services = replace(run.services, events=asyncio.Queue())
        answerer = asyncio.create_task(answer_approvals(registry, run, decision, seen))
        result = await call_tool(registry, run, "create_pull_request", {"title": "Add login"})
        answerer.cancel()
        return result

    output, is_error = asyncio.run(scenario("approve"))
    assert is_error and "Push it with git_push first" in output
    assert [event["tool"] for event in seen] == ["create_pull_request"]

    # Once GitHub has the branch, the approved call opens the pull request.
    git("update-ref", "refs/remotes/origin/feature", "HEAD", cwd=folder)
    git("branch", "--set-upstream-to=origin/feature", cwd=folder)
    output, is_error = asyncio.run(scenario("approve"))
    assert not is_error and "Opened pull request #1" in output, output
    output, is_error = asyncio.run(scenario("deny"))
    assert is_error and "declined" in output
    assert len(github.pulls) == 1
