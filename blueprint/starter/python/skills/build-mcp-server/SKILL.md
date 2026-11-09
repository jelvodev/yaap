---
name: build-mcp-server
description: Plan, build, test and evaluate an MCP server that gives agents tools for a service or API, with tool
  names and descriptions an agent picks correctly, typed inputs, paginated and concise results, errors that say what
  to do next, and a set of evaluation questions. Use this whenever you build or extend an MCP server, wrap an API or
  database as tools for an agent, design tool names or schemas, or an agent keeps calling the wrong tool or misreads
  a tool's result.
license: Apache-2.0
metadata:
  sources: anthropic-skills/skills/mcp-builder, agency-agents/specialized/specialized-mcp-builder.md
---

# Build an MCP server

An MCP server is judged by one thing: can an agent use it to finish real tasks? The agent only sees tool names,
descriptions, input schemas and results. Treat those as the user interface, design them before the code, and test
them with an agent, not only with unit tests.

## 1. Research

- **The tasks.** Write five to ten things a user will ask an agent to do with this service ("which open issues have
  no owner?", "move this card to Done"). They drive every later choice.
- **The API.** Read the service's documentation with `web_fetch`: authentication, the main resources, pagination,
  rate limits, error format.
- **The protocol and the SDK.** SDKs change between major versions. Before writing code, fetch the current README of
  the official SDK for your language and follow its patterns, not ones from memory. Use stdio for a server that runs
  on the user's machine, Streamable HTTP for a remote one.

## 2. Design the tools

Write the tool list as a table before any code: name, one-line purpose, inputs, result, read-only or not.

- **Coverage first, then shortcuts.** Cover the common operations of the API so the agent can combine them. Add a
  workflow tool (several calls in one) only for a task that is frequent and awkward to compose.
- **Names the agent can choose from.** A service prefix and a verb: `tracker_search_issues`, `tracker_get_issue`,
  `tracker_update_issue`. One job per tool; a `mode` parameter that switches behaviour is two tools in disguise.
- **Descriptions say when to use it.** What it does, when to choose it over its neighbours, and what it returns. "Use
  `tracker_search_issues` first when you only know words from the title."
- **Typed inputs with limits.** Every parameter has a type, a description, constraints (ranges, patterns, enums) and
  a default where one makes sense. Validate with the SDK's schema support (Pydantic in Python, a schema library in
  TypeScript).
- **Concise results.** Return the fields an agent needs, not the raw API response. Offer a detail level or a choice
  of format (short Markdown for reading, JSON for further processing) when both are useful. Return structured content
  next to the text when the SDK supports it.
- **Pagination always.** A `limit` with a sensible default (20 to 50), a cursor or offset, and in the result
  `has_more` and the next cursor. Never load a whole collection into one result.
- **Annotations.** Mark each tool read-only, destructive, idempotent or open-world. Clients use them to decide what
  to ask the user about; they are hints, not security.

## 3. Errors that teach

An error result says what went wrong and what to try next, in the agent's terms: "No issue 4512. Search with
`tracker_search_issues` to find its number." Return it as a tool error result; never crash the server or return an
empty success. Translate API errors (401, 404, 429) into such messages, and include the wait time for a rate limit.

## 4. Build

- Keep the structure small: an API client with authentication, one module per group of tools, shared helpers for
  errors, formatting and pagination. No duplicated request code.
- Secrets come from environment variables, never from code or tool arguments, and never appear in results or logs.
- A stdio server writes logs to stderr only; anything on stdout corrupts the protocol.
- A local HTTP server binds to `127.0.0.1` and checks the `Origin` header, against DNS rebinding. A remote one
  requires authentication on every request.
- Use the token with the least rights the tools need; a read-only server gets a read-only token.
- Use async I/O for network calls, with a timeout on each.

A minimal Python tool, to show the shape (check the SDK's current API before copying the pattern):

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("tracker")

ISSUES = {4511: {"title": "Login fails after password reset", "state": "open"}}


@mcp.tool()
def tracker_get_issue(issue_id: int) -> dict[str, str]:
    """Get one issue by its number. Use tracker_search_issues first when you only know words from its title."""
    issue = ISSUES.get(issue_id)
    if issue is None:
        raise ValueError(f"No issue {issue_id}. Search with tracker_search_issues to find its number.")
    return issue


if __name__ == "__main__":
    mcp.run()
```

## 5. Test

- **Unit tests** for each tool function: valid input, invalid input, an empty result, an API error, a second page.
  Fake the API at the HTTP edge (the `python-testing` skill).
- **The type checker and linter** on the whole server.
- **In YAAP.** Add the server under Project settings, *MCP servers* (stdio with its command, or its URL), and use its
  test: it lists the tools YAAP sees. Then ask an agent in a conversation to do the tasks from step 1, and read its
  tool calls in the trace. A tool the agent skips or misuses needs a better name or description, not a longer prompt.
  With more than five tools, check that `find_tools` finds the right one from a plain query.

## 6. Evaluate

Write about ten evaluation questions that only the server's tools can answer:

- **realistic:** something a user would really ask;
- **demanding:** each needs several tool calls, such as a search, a detail and a comparison;
- **read-only:** answering changes nothing;
- **independent:** no question depends on another;
- **verifiable and stable:** one short answer that can be compared as text and will not change next week.

Solve each one yourself with the tools to confirm the answer. Save them as the project's eval cases, so `/eval` runs
them after each change to the tools and shows whether a new description helped or hurt.

## Report

```
Tasks: <the user tasks the server supports>
Tools: <name - purpose - read-only or not>
Transport and auth: <stdio or HTTP, where the secret comes from>
Tests: <command and result>
Agent check: <tasks tried in YAAP, tools that were misused and what changed>
Evaluation: <number of questions, how many answered correctly>
```

Adapted from the `mcp-builder` skill of anthropics/skills under Apache-2.0: only its Markdown guidance, rewritten and
shortened, without its scripts, reference code or vendor-specific steps, and with YAAP's MCP server settings, trace
and eval cases added.
