"""GitHub's REST API for repositories, templates, issues and pull requests.

One small client over httpx. Tests pass an httpx.MockTransport as a fake GitHub. Every error becomes a GitHubError
with a message that can be shown; a 403 or 404 names what the token lacks when GitHub says so
(X-Accepted-GitHub-Permissions for fine-grained tokens, X-Accepted-OAuth-Scopes for classic ones).
"""

import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx

API = "https://api.github.com"
TIMEOUT = 20.0
REMOTE = re.compile(r"^(?:https://github\.com/|git@github\.com:)(?P<owner>[A-Za-z0-9_.-]+)/(?P<name>[A-Za-z0-9_.-]+?)(?:\.git)?/?$")
# What YAAP's GitHub work needs (choice 15): a classic token with repo, or a fine-grained one with these permissions.
CLASSIC_SCOPES = ("repo",)
FINE_GRAINED_NEEDS = ("Administration: write", "Contents: write", "Pull requests: write")
MERGE_METHODS = ("squash", "merge", "rebase")
# The transport every client uses when none is given; tests set a fake GitHub here.
DEFAULT_TRANSPORT: httpx.BaseTransport | None = None


class GitHubError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def repository_name(remote_url: str | None) -> str | None:
    """owner/name of a GitHub remote, or None when it is not on GitHub."""
    match = REMOTE.match((remote_url or "").strip())
    return f"{match.group('owner')}/{match.group('name')}" if match else None


@dataclass
class TokenCheck:
    # classic, fine_grained, or none (no token, or GitHub refused it).
    kind: str
    login: str | None = None
    scopes: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    message: str = ""


@dataclass
class GitHubRepository:
    full_name: str
    private: bool
    default_branch: str
    is_template: bool
    html_url: str
    clone_url: str
    description: str
    updated_at: str | None


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    body: str
    state: str
    labels: tuple[str, ...]
    html_url: str
    author: str
    updated_at: str | None


@dataclass
class PullRequest:
    number: int
    title: str
    state: str
    draft: bool
    merged: bool
    author: str
    head: str
    base: str
    html_url: str
    updated_at: str | None
    body: str = ""
    head_sha: str = ""
    mergeable: bool | None = None


def repository_of(item: dict[str, Any]) -> GitHubRepository:
    return GitHubRepository(
        full_name=str(item.get("full_name", "")),
        private=bool(item.get("private")),
        default_branch=str(item.get("default_branch") or "main"),
        is_template=bool(item.get("is_template")),
        html_url=str(item.get("html_url", "")),
        clone_url=str(item.get("clone_url", "")),
        description=str(item.get("description") or ""),
        updated_at=item.get("updated_at"),
    )


def pull_of(item: dict[str, Any]) -> PullRequest:
    return PullRequest(
        number=int(item["number"]),
        title=str(item.get("title", "")),
        state=str(item.get("state", "open")),
        draft=bool(item.get("draft")),
        merged=bool(item.get("merged") or item.get("merged_at")),
        author=str((item.get("user") or {}).get("login", "")),
        head=str((item.get("head") or {}).get("ref", "")),
        base=str((item.get("base") or {}).get("ref", "")),
        html_url=str(item.get("html_url", "")),
        updated_at=item.get("updated_at"),
        body=str(item.get("body") or ""),
        head_sha=str((item.get("head") or {}).get("sha", "")),
        mergeable=item.get("mergeable"),
    )


def issue_of(item: dict[str, Any]) -> Issue:
    labels = [label.get("name") if isinstance(label, dict) else label for label in item.get("labels") or []]
    return Issue(
        number=int(item["number"]),
        title=str(item.get("title") or ""),
        body=str(item.get("body") or ""),
        state=str(item.get("state") or "open"),
        labels=tuple(str(label) for label in labels if isinstance(label, str) and label),
        html_url=str(item.get("html_url") or ""),
        author=str((item.get("user") or {}).get("login", "")),
        updated_at=item.get("updated_at"),
    )


def problem_text(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        body = {}
    message = str(body.get("message") or response.reason_phrase or "GitHub refused the request")
    details = [str(error.get("message") or error.get("code") or "") for error in body.get("errors", []) if isinstance(error, dict)]
    details = [detail for detail in details if detail]
    text = message + (f" ({'; '.join(details)})" if details else "")
    needs = response.headers.get("X-Accepted-GitHub-Permissions") or response.headers.get("X-Accepted-OAuth-Scopes")
    if response.status_code in (403, 404) and needs:
        text += f". The GitHub token needs: {needs}."
    return text


class GitHubClient:
    def __init__(self, token: str | None, transport: httpx.BaseTransport | None = None) -> None:
        if not token:
            raise GitHubError("No GitHub token in this environment. Add one in Settings › Git and GitHub.")
        self.token = token
        self.http = httpx.Client(
            base_url=API,
            transport=transport or DEFAULT_TRANSPORT,
            timeout=TIMEOUT,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "YAAP",
            },
        )

    def __enter__(self) -> "GitHubClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.http.close()

    def request(self, method: str, path: str, **options: Any) -> httpx.Response:
        try:
            response = self.http.request(method, path, **options)
        except httpx.HTTPError as error:
            raise GitHubError(f"GitHub cannot be reached: {error}") from error
        if response.status_code >= 400:
            raise GitHubError(problem_text(response), response.status_code)
        return response

    def check_token(self) -> TokenCheck:
        try:
            response = self.request("GET", "/user")
        except GitHubError as error:
            return TokenCheck(kind="none", message=f"GitHub refused the token: {error}")
        login = str(response.json().get("login", ""))
        header = response.headers.get("X-OAuth-Scopes")
        if header is not None:
            scopes = [scope.strip() for scope in header.split(",") if scope.strip()]
            missing = [scope for scope in CLASSIC_SCOPES if scope not in scopes]
            message = (
                "Classic token: it may create repositories, push and work with pull requests."
                if not missing
                else f"Classic token without {', '.join(missing)}: creating repositories and pull requests will fail."
            )
            return TokenCheck(kind="classic", login=login, scopes=scopes, missing=missing, message=message)
        return TokenCheck(
            kind="fine_grained",
            login=login,
            message=(
                "Fine-grained token: GitHub does not list its permissions. It needs "
                + ", ".join(FINE_GRAINED_NEEDS)
                + " on the repositories YAAP works with; when one is missing, the action says which."
            ),
        )

    # --- Repositories -------------------------------------------------------------------------------------------

    def repositories(self, limit: int = 200) -> list[GitHubRepository]:
        found: list[GitHubRepository] = []
        page = 1
        while len(found) < limit:
            items = self.request(
                "GET",
                "/user/repos",
                params={"per_page": 100, "page": page, "sort": "updated", "affiliation": "owner,collaborator,organization_member"},
            ).json()
            found += [repository_of(item) for item in items]
            if len(items) < 100:
                break
            page += 1
        return found[:limit]

    def repository(self, full_name: str) -> GitHubRepository:
        return repository_of(self.request("GET", f"/repos/{full_name}").json())

    def create_repository(
        self, name: str, private: bool, description: str = "", template: str | None = None, owner: str | None = None
    ) -> GitHubRepository:
        if template:
            body: dict[str, Any] = {"name": name, "private": private, "description": description}
            if owner:
                body["owner"] = owner
            return repository_of(self.request("POST", f"/repos/{template}/generate", json=body).json())
        body = {"name": name, "private": private, "description": description, "auto_init": True}
        path = f"/orgs/{owner}/repos" if owner else "/user/repos"
        return repository_of(self.request("POST", path, json=body).json())

    def wait_until_filled(self, full_name: str, seconds: float = 20.0, pause: float = 1.0) -> None:
        """GitHub copies a template in the background: wait until the new repository has a commit to clone."""
        deadline = time.monotonic() + seconds
        while True:
            try:
                self.request("GET", f"/repos/{full_name}/commits", params={"per_page": 1})
                return
            except GitHubError as error:
                if error.status not in (404, 409) or time.monotonic() >= deadline:
                    raise GitHubError(f"{full_name} was created, but GitHub has not filled it yet: {error}") from error
            time.sleep(pause)

    def set_template(self, full_name: str, is_template: bool) -> GitHubRepository:
        return repository_of(self.request("PATCH", f"/repos/{full_name}", json={"is_template": is_template}).json())

    # --- Issues ------------------------------------------------------------------------------------------------

    def issues(
        self,
        full_name: str,
        state: str = "open",
        labels: Sequence[str] = (),
        since: str | None = None,
        limit: int = 100,
    ) -> list[Issue]:
        """The repository's issues, newest updated first; pull requests (which GitHub lists as issues) are left out."""
        params: dict[str, Any] = {"state": state, "per_page": 100, "sort": "updated", "direction": "desc"}
        if labels:
            params["labels"] = ",".join(labels)
        if since:
            params["since"] = since
        found: list[Issue] = []
        page = 1
        while len(found) < limit:
            items = self.request("GET", f"/repos/{full_name}/issues", params={**params, "page": page}).json()
            found += [issue_of(item) for item in items if isinstance(item, dict) and "pull_request" not in item]
            if len(items) < 100:
                break
            page += 1
        return found[:limit]

    def issue(self, full_name: str, number: int) -> Issue | None:
        """One issue, or None when the number is a pull request."""
        item = self.request("GET", f"/repos/{full_name}/issues/{number}").json()
        return None if "pull_request" in item else issue_of(item)

    # --- Pull requests ------------------------------------------------------------------------------------------

    def pulls(self, full_name: str, state: str = "open") -> list[PullRequest]:
        items = self.request("GET", f"/repos/{full_name}/pulls", params={"state": state, "per_page": 50}).json()
        return [pull_of(item) for item in items]

    def pull(self, full_name: str, number: int) -> PullRequest:
        return pull_of(self.request("GET", f"/repos/{full_name}/pulls/{number}").json())

    def pull_files(self, full_name: str, number: int) -> list[dict[str, Any]]:
        items = self.request("GET", f"/repos/{full_name}/pulls/{number}/files", params={"per_page": 100}).json()
        return [
            {
                "path": str(item.get("filename", "")),
                "status": str(item.get("status", "")),
                "additions": int(item.get("additions") or 0),
                "deletions": int(item.get("deletions") or 0),
                "patch": str(item.get("patch") or ""),
            }
            for item in items
        ]

    def checks(self, full_name: str, sha: str) -> list[dict[str, Any]]:
        if not sha:
            return []
        try:
            body = self.request("GET", f"/repos/{full_name}/commits/{sha}/check-runs", params={"per_page": 50}).json()
        except GitHubError:
            return []
        return [
            {"name": str(run.get("name", "")), "status": str(run.get("status", "")), "conclusion": run.get("conclusion")}
            for run in body.get("check_runs", [])
        ]

    def create_pull(self, full_name: str, head: str, base: str, title: str, body: str, draft: bool = False) -> PullRequest:
        payload = {"head": head, "base": base, "title": title, "body": body, "draft": draft}
        return pull_of(self.request("POST", f"/repos/{full_name}/pulls", json=payload).json())

    def merge_pull(self, full_name: str, number: int, method: str = "squash") -> str:
        if method not in MERGE_METHODS:
            raise GitHubError(f"Merge with one of: {', '.join(MERGE_METHODS)}.")
        result = self.request("PUT", f"/repos/{full_name}/pulls/{number}/merge", json={"merge_method": method}).json()
        return str(result.get("sha", ""))

    def delete_branch(self, full_name: str, branch: str) -> None:
        self.request("DELETE", f"/repos/{full_name}/git/refs/heads/{quote(branch, safe='/')}")
