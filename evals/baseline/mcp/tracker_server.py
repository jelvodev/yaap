"""A small issue tracker as an MCP server over stdio, for the baseline eval set of YAAP (plan 46.1).

It serves the Stockroom project's issues from `issues.json` next to this file. Changes (new issues, comments,
transitions) live only as long as the process; every start begins from the file again, so eval plays are repeatable.
Standard library only, newline-delimited JSON-RPC 2.0 as MCP's stdio transport specifies.

Run: python3 tracker_server.py [path/to/issues.json]
"""

import copy
import json
import sys
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = "2025-06-18"
DATA_FILE = Path(__file__).with_name("issues.json")
STATUSES = ("open", "in_progress", "in_review", "done", "wont_fix")
PRIORITIES = ("low", "medium", "high", "urgent")


def prop(kind: str, description: str, **extra: Any) -> dict[str, Any]:
    return {"type": kind, "description": description, **extra}


KEY = prop("string", "The issue key, such as SR-12: the project key, a hyphen and the number.")
LIMIT = prop("integer", "The most results to return, from 1 to 100. Default 20.", minimum=1, maximum=100)


def tool(name: str, description: str, properties: dict[str, Any], required: list[str], read_only: bool) -> dict:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": read_only},
    }


TOOLS = [
    tool("list_projects", "List the projects in the tracker with their key, name and lead.", {}, [], True),
    tool(
        "get_project",
        "Get one project with its description, components, lead and the number of open issues per status.",
        {"key": prop("string", "The project key, such as SR.")},
        ["key"],
        True,
    ),
    tool(
        "search_issues",
        "Search issues by text in their title, description and comments, optionally narrowed by status, label, "
        "assignee or project. Returns the key, title, status, priority and assignee of each match, best first.",
        {
            "query": prop("string", "Words to look for; all must occur. Empty matches every issue."),
            "project": prop("string", "Only issues of this project key."),
            "status": prop("string", "Only issues in this status.", enum=list(STATUSES)),
            "label": prop("string", "Only issues with this label."),
            "assignee": prop("string", "Only issues assigned to this user name."),
            "limit": LIMIT,
        },
        [],
        True,
    ),
    tool(
        "list_issues",
        "List the issues of a project, newest first unless sorted otherwise. Use search_issues to find issues by text.",
        {
            "project": prop("string", "The project key, such as SR."),
            "status": prop("string", "Only issues in this status.", enum=list(STATUSES)),
            "sort": prop("string", "Sort order. Default newest.", enum=["newest", "oldest", "priority", "updated"]),
            "limit": LIMIT,
        },
        ["project"],
        True,
    ),
    tool(
        "get_issue",
        "Get one issue in full: title, description, status, priority, labels, assignee, reporter, milestone, links "
        "to other issues and the number of comments. Read the comments with list_comments.",
        {"key": KEY},
        ["key"],
        True,
    ),
    tool(
        "create_issue",
        "Create an issue in a project. It starts in status open, unassigned unless an assignee is given.",
        {
            "project": prop("string", "The project key, such as SR."),
            "title": prop("string", "A short title, at most 120 characters."),
            "description": prop("string", "What happens, what was expected, and how to reproduce it. Markdown."),
            "priority": prop("string", "Default medium.", enum=list(PRIORITIES)),
            "labels": prop("array", "Labels to add.", items={"type": "string"}),
            "assignee": prop("string", "The user name to assign it to."),
        },
        ["project", "title"],
        False,
    ),
    tool(
        "update_issue",
        "Change the title, description or priority of an issue. Fields that are left out keep their value.",
        {
            "key": KEY,
            "title": prop("string", "The new title."),
            "description": prop("string", "The new description, replacing the old one. Markdown."),
            "priority": prop("string", "The new priority.", enum=list(PRIORITIES)),
        },
        ["key"],
        False,
    ),
    tool(
        "transition_issue",
        "Move an issue to another status. Moving to done or wont_fix needs a resolution comment.",
        {
            "key": KEY,
            "status": prop("string", "The new status.", enum=list(STATUSES)),
            "comment": prop("string", "Why; required for done and wont_fix."),
        },
        ["key", "status"],
        False,
    ),
    tool(
        "assign_issue",
        "Assign an issue to a user, or unassign it with an empty assignee.",
        {"key": KEY, "assignee": prop("string", "The user name, or empty to unassign.")},
        ["key", "assignee"],
        False,
    ),
    tool(
        "list_comments",
        "List the comments on an issue, oldest first, with author and date.",
        {"key": KEY, "limit": LIMIT},
        ["key"],
        True,
    ),
    tool(
        "add_comment",
        "Add a comment to an issue. Markdown; mention a user with @name.",
        {"key": KEY, "body": prop("string", "The comment text.")},
        ["key", "body"],
        False,
    ),
    tool("list_labels", "List the labels in use with how many issues carry each.", {}, [], True),
    tool(
        "add_label",
        "Add a label to an issue; a new label is created when it does not exist.",
        {"key": KEY, "label": prop("string", "The label, lowercase with hyphens.")},
        ["key", "label"],
        False,
    ),
    tool(
        "remove_label",
        "Remove a label from an issue.",
        {"key": KEY, "label": prop("string", "The label to remove.")},
        ["key", "label"],
        False,
    ),
    tool(
        "list_milestones",
        "List the milestones of a project with their due date and progress.",
        {"project": prop("string", "The project key, such as SR.")},
        ["project"],
        True,
    ),
    tool(
        "get_milestone",
        "Get one milestone with its description, due date and the keys of its issues.",
        {"project": prop("string", "The project key."), "name": prop("string", "The milestone name, such as 0.5.")},
        ["project", "name"],
        True,
    ),
    tool("list_users", "List the users of the tracker with their name and role.", {}, [], True),
    tool(
        "get_user",
        "Get one user with their role and the keys of the open issues assigned to them.",
        {"name": prop("string", "The user name.")},
        ["name"],
        True,
    ),
    tool(
        "link_issues",
        "Link two issues: blocks, relates to or duplicates.",
        {
            "key": KEY,
            "other": prop("string", "The key of the other issue."),
            "kind": prop(
                "string", "How the first issue relates to the other.", enum=["blocks", "relates", "duplicates"]
            ),
        },
        ["key", "other", "kind"],
        False,
    ),
    tool(
        "get_issue_history",
        "The changes of an issue over time: status, assignee, priority and label changes, with who and when.",
        {"key": KEY},
        ["key"],
        True,
    ),
    tool(
        "list_attachments",
        "List the files attached to an issue with their name, size and who added them. Contents are not served.",
        {"key": KEY},
        ["key"],
        True,
    ),
    tool(
        "watch_issue",
        "Start or stop watching an issue, to be notified of its changes.",
        {"key": KEY, "watch": prop("boolean", "True to watch, false to stop. Default true.")},
        ["key"],
        False,
    ),
]


class Tracker:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = copy.deepcopy(data)
        self.issues: dict[str, dict[str, Any]] = {issue["key"]: issue for issue in self.data["issues"]}

    def issue(self, key: str) -> dict[str, Any]:
        found = self.issues.get(str(key).strip().upper())
        if found is None:
            raise LookupError(f"There is no issue {key}.")
        return found

    def project(self, key: str) -> dict[str, Any]:
        for project in self.data["projects"]:
            if project["key"] == str(key).strip().upper():
                return project
        raise LookupError(f"There is no project {key}.")

    def call(self, name: str, args: dict[str, Any]) -> Any:
        handler = getattr(self, f"do_{name}", None)
        if handler is None:
            raise LookupError(f"Unknown tool {name}.")
        return handler(args)

    @staticmethod
    def brief(issue: dict[str, Any]) -> dict[str, Any]:
        keys = ("key", "title", "status", "priority", "assignee")
        return {key: issue.get(key) for key in keys}

    def do_list_projects(self, args: dict[str, Any]) -> Any:
        return [{key: p[key] for key in ("key", "name", "lead")} for p in self.data["projects"]]

    def do_get_project(self, args: dict[str, Any]) -> Any:
        project = dict(self.project(args["key"]))
        counts: dict[str, int] = {}
        for issue in self.issues.values():
            if issue["key"].startswith(project["key"] + "-"):
                counts[issue["status"]] = counts.get(issue["status"], 0) + 1
        project["issues_per_status"] = counts
        return project

    def do_search_issues(self, args: dict[str, Any]) -> Any:
        words = str(args.get("query", "")).casefold().split()
        found = []
        for issue in self.issues.values():
            text = " ".join(
                [issue["title"], issue["description"], *(c["body"] for c in issue.get("comments", []))]
            ).casefold()
            if not all(word in text for word in words):
                continue
            if args.get("project") and not issue["key"].startswith(str(args["project"]).upper() + "-"):
                continue
            if args.get("status") and issue["status"] != args["status"]:
                continue
            if args.get("label") and args["label"] not in issue.get("labels", []):
                continue
            if args.get("assignee") and issue.get("assignee") != args["assignee"]:
                continue
            score = sum(issue["title"].casefold().count(word) * 3 + text.count(word) for word in words)
            found.append((-score, issue["key"], issue))
        found.sort(key=lambda entry: (entry[0], entry[1]))
        return [self.brief(issue) for _, _, issue in found[: int(args.get("limit", 20))]]

    def do_list_issues(self, args: dict[str, Any]) -> Any:
        prefix = str(args["project"]).upper() + "-"
        issues = [i for i in self.issues.values() if i["key"].startswith(prefix)]
        if args.get("status"):
            issues = [i for i in issues if i["status"] == args["status"]]
        order = args.get("sort", "newest")
        if order == "priority":
            issues.sort(key=lambda i: -PRIORITIES.index(i["priority"]))
        else:
            field = "created" if order in ("newest", "oldest") else order
            issues.sort(key=lambda i: i.get(field, i["created"]), reverse=order != "oldest")
        return [self.brief(issue) for issue in issues[: int(args.get("limit", 20))]]

    def do_get_issue(self, args: dict[str, Any]) -> Any:
        issue = {key: value for key, value in self.issue(args["key"]).items() if key not in ("comments", "history")}
        issue["comment_count"] = len(self.issue(args["key"]).get("comments", []))
        return issue

    def do_create_issue(self, args: dict[str, Any]) -> Any:
        project = self.project(args["project"])
        numbers = [int(k.split("-")[1]) for k in self.issues if k.startswith(project["key"] + "-")]
        key = f"{project['key']}-{max(numbers, default=0) + 1}"
        self.issues[key] = {
            "key": key,
            "title": str(args["title"])[:120],
            "description": args.get("description", ""),
            "status": "open",
            "priority": args.get("priority", "medium"),
            "labels": list(args.get("labels", [])),
            "assignee": args.get("assignee"),
            "reporter": "yaap",
            "created": "2026-11-08",
            "comments": [],
        }
        return self.brief(self.issues[key])

    def do_update_issue(self, args: dict[str, Any]) -> Any:
        issue = self.issue(args["key"])
        for field in ("title", "description", "priority"):
            if field in args:
                issue[field] = args[field]
        return self.brief(issue)

    def do_transition_issue(self, args: dict[str, Any]) -> Any:
        issue = self.issue(args["key"])
        if args["status"] not in STATUSES:
            raise ValueError(f"Unknown status {args['status']}.")
        if args["status"] in ("done", "wont_fix") and not args.get("comment"):
            raise ValueError("Moving to done or wont_fix needs a comment.")
        issue["status"] = args["status"]
        if args.get("comment"):
            issue.setdefault("comments", []).append({"author": "yaap", "date": "2026-11-08", "body": args["comment"]})
        return self.brief(issue)

    def do_assign_issue(self, args: dict[str, Any]) -> Any:
        issue = self.issue(args["key"])
        issue["assignee"] = args["assignee"] or None
        return self.brief(issue)

    def do_list_comments(self, args: dict[str, Any]) -> Any:
        return self.issue(args["key"]).get("comments", [])[: int(args.get("limit", 20))]

    def do_add_comment(self, args: dict[str, Any]) -> Any:
        comment = {"author": "yaap", "date": "2026-11-08", "body": str(args["body"])}
        self.issue(args["key"]).setdefault("comments", []).append(comment)
        return comment

    def do_list_labels(self, args: dict[str, Any]) -> Any:
        counts: dict[str, int] = {}
        for issue in self.issues.values():
            for label in issue.get("labels", []):
                counts[label] = counts.get(label, 0) + 1
        return dict(sorted(counts.items()))

    def do_add_label(self, args: dict[str, Any]) -> Any:
        labels = self.issue(args["key"]).setdefault("labels", [])
        if args["label"] not in labels:
            labels.append(args["label"])
        return labels

    def do_remove_label(self, args: dict[str, Any]) -> Any:
        issue = self.issue(args["key"])
        issue["labels"] = [label for label in issue.get("labels", []) if label != args["label"]]
        return issue["labels"]

    def do_list_milestones(self, args: dict[str, Any]) -> Any:
        return [m for m in self.data["milestones"] if m["project"] == str(args["project"]).upper()]

    def do_get_milestone(self, args: dict[str, Any]) -> Any:
        for milestone in self.do_list_milestones(args):
            if milestone["name"] == args["name"]:
                keys = [i["key"] for i in self.issues.values() if i.get("milestone") == milestone["name"]]
                return {**milestone, "issues": sorted(keys)}
        raise LookupError(f"There is no milestone {args['name']}.")

    def do_list_users(self, args: dict[str, Any]) -> Any:
        return self.data["users"]

    def do_get_user(self, args: dict[str, Any]) -> Any:
        for user in self.data["users"]:
            if user["name"] == args["name"]:
                assigned = [i["key"] for i in self.issues.values() if i.get("assignee") == user["name"]]
                return {**user, "open_issues": sorted(k for k in assigned if self.issues[k]["status"] != "done")}
        raise LookupError(f"There is no user {args['name']}.")

    def do_link_issues(self, args: dict[str, Any]) -> Any:
        self.issue(args["other"])
        links = self.issue(args["key"]).setdefault("links", [])
        links.append({"kind": args["kind"], "key": str(args["other"]).upper()})
        return links

    def do_get_issue_history(self, args: dict[str, Any]) -> Any:
        return self.issue(args["key"]).get("history", [])

    def do_list_attachments(self, args: dict[str, Any]) -> Any:
        return self.issue(args["key"]).get("attachments", [])

    def do_watch_issue(self, args: dict[str, Any]) -> Any:
        self.issue(args["key"])
        return {"key": str(args["key"]).upper(), "watching": bool(args.get("watch", True))}


def respond(message_id: Any, result: Any = None, error: dict[str, Any] | None = None) -> None:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": message_id}
    body["error" if error else "result"] = error or result
    sys.stdout.write(json.dumps(body) + "\n")
    sys.stdout.flush()


def tool_result(value: Any, is_error: bool = False) -> dict[str, Any]:
    text = value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def serve(tracker: Tracker) -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        message = json.loads(line)
        if "id" not in message:
            continue
        method, params = message.get("method"), message.get("params") or {}
        if method == "initialize":
            info = {"name": "stockroom-tracker", "version": "1.0"}
            result = {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}}, "serverInfo": info}
            respond(message["id"], result)
        elif method == "tools/list":
            respond(message["id"], {"tools": TOOLS})
        elif method == "tools/call":
            try:
                result = tracker.call(str(params.get("name")), params.get("arguments") or {})
                respond(message["id"], tool_result(result))
            except (LookupError, ValueError, KeyError) as error:
                respond(message["id"], tool_result(str(error), is_error=True))
        elif method == "ping":
            respond(message["id"], {})
        else:
            respond(message["id"], error={"code": -32601, "message": f"Unknown method {method}."})


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DATA_FILE
    serve(Tracker(json.loads(path.read_text(encoding="utf-8"))))
