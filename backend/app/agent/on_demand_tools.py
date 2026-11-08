"""Tools that add other tools to the run (efficiency): `open_browser` adds the browser's tools, and
`find_tools` adds the tools of outside MCP servers that match a query.

A run offers these instead of the tools they add, so a reply that never needs them does not carry their definitions
on every model call. The tools they add go through the gate like any other, with their own permissions.
"""

from typing import Any

from app.agent.runs import AgentRun
from app.agent.tool_catalog import best_matches, short_description
from app.agent.toolkit import STRING, Tool, ToolError, schema, text_argument

BROWSER_PREFIX = "browser__"


async def open_browser(run: AgentRun, _arguments: dict[str, Any]) -> str:
    tools = run.catalog.browser
    if not tools:
        raise ToolError("The browser is not available in this conversation.")
    joined = tools <= run.state.joined_tools
    run.state.joined_tools |= tools
    names = ", ".join(sorted(tools))
    if joined:
        return f"The browser tools are already available: {names}."
    return (
        f"The browser tools are available now: {names}. Start with {BROWSER_PREFIX}browser_navigate; pages on "
        "localhost (the project's dev servers) open without asking."
    )


async def find_tools(run: AgentRun, arguments: dict[str, Any]) -> str:
    query = text_argument(arguments, "query")
    server = text_argument(arguments, "server", required=False)
    hidden = [listing for listing in run.catalog.servers if not listing.listed]
    if server:
        known = {listing.name: listing for listing in run.catalog.servers}
        if server not in known:
            names = ", ".join(listing.name for listing in hidden) or "none"
            raise ToolError(f"No MCP server {server}. Servers whose tools find_tools adds: {names}.")
        if known[server].listed:
            return f"All tools of {server} are offered already; call them directly."
        hidden = [known[server]]
    candidates = {
        name: run.extra_tools[name].description
        for listing in hidden
        for name in listing.tools
        if name in run.extra_tools
    }
    found = best_matches(query, candidates)
    if not found:
        where = server or ", ".join(listing.name for listing in hidden)
        return f"No tools of {where} matched '{query}'. Try other words, such as a verb and an object."
    run.state.joined_tools.update(found)
    lines = [f"- {name}: {short_description(candidates[name])}" for name in found]
    added = f"Added {len(found)} tool{'' if len(found) == 1 else 's'}, available from your next step:"
    return "\n".join([added, *lines])


ON_DEMAND_TOOLS = [
    Tool(
        "open_browser",
        "Make the headless browser's tools available (open pages, click, type, screenshots), to check a web app or "
        "a site. Call it once before using the browser.",
        schema({}, []),
        "auto",
        open_browser,
        read_only=True,
    ),
    Tool(
        "find_tools",
        "Find tools of the outside MCP servers listed in the system prompt by what they do (for example 'create "
        "issue'), optionally on one server. Adds the best matches to your tools and returns their names.",
        schema({"query": STRING, "server": STRING}, ["query"]),
        "auto",
        find_tools,
        scope="always",
        read_only=True,
    ),
]
