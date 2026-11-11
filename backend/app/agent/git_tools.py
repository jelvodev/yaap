"""The git tools: status, diff, commit and push of the project, and a GitHub pull request.

They work on the project folder in YAAP; on a computer over SSH git runs as a command instead.
"""

from typing import Any

import anyio

from app.agent.runs import AgentRun
from app.agent.tool_context import executor, project_root
from app.agent.toolkit import STRING, Tool, ToolError, schema, text_argument, truncate
from app.git.service import RepositoryStatus, commit_all, list_branches, push, repository_diff, repository_status
from app.git.worktree import (
    commit_all as commit_all_on_target,
    list_branches as list_branches_on_target,
    push_commits,
    repository_diff as repository_diff_on_target,
    repository_status as repository_status_on_target,
)
from app.github.client import GitHubClient, GitHubError, repository_name


async def git_status(run: AgentRun, _arguments: dict[str, Any]) -> str:
    if run.target is not None:
        target_executor = executor(run)
        status = await repository_status_on_target(target_executor)
        branches = await list_branches_on_target(target_executor)
    else:
        status = await anyio.to_thread.run_sync(repository_status, project_root(run))
        branches = await anyio.to_thread.run_sync(list_branches, project_root(run))
    lines = [
        f"Branch: {status.branch or 'detached'}",
        f"Upstream: {status.upstream or 'none'} (ahead {status.ahead}, behind {status.behind})",
    ]
    others = [branch.name for branch in branches if not branch.current]
    if others:
        lines.append(f"Other local branches: {', '.join(others[:30])}")
    lines += [f"{change.state}: {change.path}" for change in status.changes] or ["No changes."]
    return "\n".join(lines)


async def git_diff(run: AgentRun, _arguments: dict[str, Any]) -> str:
    if run.target is not None:
        diff = await repository_diff_on_target(executor(run))
    else:
        diff = await anyio.to_thread.run_sync(repository_diff, project_root(run))
    return truncate(diff) if diff.strip() else "No changes."


async def git_commit(run: AgentRun, arguments: dict[str, Any]) -> str:
    message = text_argument(arguments, "message")
    if run.target is not None:
        commit = await commit_all_on_target(executor(run), message, run.git_author)
    else:
        commit = await anyio.to_thread.run_sync(commit_all, project_root(run), message, run.git_author)
    return f"Committed all changes as {commit}."


async def git_push(run: AgentRun, _arguments: dict[str, Any]) -> str:
    if run.target is not None:
        await push_commits(executor(run), project_root(run), run.github_token)
    else:
        await anyio.to_thread.run_sync(push, project_root(run), run.github_token)
    return "Pushed."


def open_pull(run: AgentRun, arguments: dict[str, Any], status: RepositoryStatus | None = None) -> str:
    folder = project_root(run)
    status = status or repository_status(folder)
    name = repository_name(status.remote_url)
    if name is None:
        raise ToolError("This project's remote is not a GitHub repository.")
    if not status.branch:
        raise ToolError("The project is not on a branch.")
    if not status.upstream or status.ahead:
        raise ToolError("The branch is not on GitHub with all its commits yet. Push it with git_push first.")
    try:
        with GitHubClient(run.github_token) as client:
            base = text_argument(arguments, "base", required=False).strip() or client.repository(name).default_branch
            if base == status.branch:
                raise ToolError(f"The project is on {base}, the branch to merge into. Work on a branch of its own.")
            pull = client.create_pull(
                name,
                status.branch,
                base,
                text_argument(arguments, "title"),
                text_argument(arguments, "body", required=False),
                bool(arguments.get("draft")),
            )
    except GitHubError as error:
        raise ToolError(str(error)) from error
    return f"Opened pull request #{pull.number}: {pull.html_url}"


async def create_pull_request(run: AgentRun, arguments: dict[str, Any]) -> str:
    if run.target is not None:
        status = await repository_status_on_target(executor(run))
        return await anyio.to_thread.run_sync(open_pull, run, arguments, status)
    return await anyio.to_thread.run_sync(open_pull, run, arguments)


GIT_TOOLS = [
    Tool(
        "git_status",
        "Show the project's branch, sync state and changed files.",
        schema({}, []),
        "auto",
        git_status,
        read_only=True,
    ),
    Tool(
        "git_diff",
        "Show all uncommitted changes of the project, including new files.",
        schema({}, []),
        "auto",
        git_diff,
        read_only=True,
    ),
    Tool(
        "git_commit",
        "Commit all changes of the project with a message in the work language named in the system prompt.",
        schema({"message": STRING}, ["message"]),
        "auto",
        git_commit,
    ),
    Tool(
        "git_push",
        "Push the project's commits to its GitHub remote. The user approves each push.",
        schema({}, []),
        "ask",
        git_push,
        describe=lambda _arguments: "Push the project's commits to GitHub",
    ),
    Tool(
        "create_pull_request",
        "Open a GitHub pull request from the project's current branch into base (default: the repository's default "
        "branch). Push the branch with git_push first. Write the title and body in the work language. The user "
        "approves it.",
        schema(
            {"title": STRING, "body": STRING, "base": STRING, "draft": {"type": "boolean"}},
            ["title"],
        ),
        "ask",
        create_pull_request,
        describe=lambda arguments: f"Open pull request: {arguments.get('title', '')}",
    ),
]
