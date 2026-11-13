"""The web tools: fetch a page and search the web. What they return counts as outside content."""

import html
import http.client
import re
from typing import Any

import anyio

from app.agent import tool_context
from app.agent.guard import note_outside
from app.agent.runs import AgentRun
from app.agent.toolkit import STRING, Tool, ToolError, schema, text_argument, truncate
from app.targets.base import TargetError
from app.websearch.runner_fetch import OFFLINE, fetch_place, fetch_with
from app.websearch.safe_fetch import fetch_public
from app.websearch.service import SearchError, format_results

MAX_FETCH_BYTES = 2 * 1024 * 1024
MAX_WEB_RESULTS = 20


def html_to_text(page: str) -> str:
    page = re.sub(r"(?is)<(script|style|noscript|svg)\b.*?</\1>", " ", page)
    page = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr|section|article)>", "\n", page)
    page = re.sub(r"(?s)<[^>]+>", " ", page)
    page = html.unescape(page)
    page = re.sub(r"[ \t]+", " ", page)
    return re.sub(r"\n\s*\n+", "\n\n", page).strip()


async def fetch_for(run: AgentRun, url: str) -> tuple[str, str, str]:
    """Fetch where the run's commands run (`runner_fetch.fetch_place`): content type, text and final URL."""
    kind = getattr(run.target, "kind", None)
    online = bool(getattr(getattr(run.target, "spec", None), "network", True))
    place = fetch_place(kind, online)
    if place == "refused":
        raise ToolError(OFFLINE.get(str(kind), "This place has no network access."))
    if place == "process":
        return await anyio.to_thread.run_sync(fetch_public, url, MAX_FETCH_BYTES)
    system = str(getattr(getattr(run.target, "settings", None), "system", "linux"))
    where = "the runner" if place == "runner" else tool_context.target_label(run)
    return await fetch_with(tool_context.executor(run), url, MAX_FETCH_BYTES, system, where)


async def web_fetch(run: AgentRun, arguments: dict[str, Any]) -> str:
    url = text_argument(arguments, "url")
    if not re.match(r"^https?://", url):
        raise ToolError("Only http and https URLs can be fetched.")

    try:
        content_type, body, final_url = await fetch_for(run, url)
    except (OSError, http.client.HTTPException, ValueError, TargetError) as error:
        raise ToolError(f"Fetching the page failed: {error}") from error
    text = html_to_text(body) if "html" in content_type else body
    note_outside(run, f"web page {final_url}", text)
    result = truncate(text, 50_000)
    return f"Fetched from {final_url}\n\n{result}" if final_url != url else result


async def web_search(run: AgentRun, arguments: dict[str, Any]) -> str:
    if run.web_search is None:
        raise ToolError("Web search needs the search service (SearXNG), which this installation does not have.")
    query = text_argument(arguments, "query")
    try:
        count = int(arguments.get("count") or 8)
    except (TypeError, ValueError) as error:
        raise ToolError("count must be a number.") from error
    try:
        results = await anyio.to_thread.run_sync(run.web_search.search, query, max(1, min(count, MAX_WEB_RESULTS)))
    except SearchError as error:
        raise ToolError(str(error)) from error
    text = format_results(query, results)
    note_outside(run, f"web search for {query}", text)
    return text


WEB_TOOLS = [
    Tool(
        "web_fetch",
        "Fetch a web page or file over http(s) and return it as text. The user approves each fetch.",
        schema({"url": STRING}, ["url"]),
        "ask",
        web_fetch,
        describe=lambda arguments: f"Fetch: {arguments.get('url', '')}",
        scope="always",
    ),
    Tool(
        "web_search",
        "Search the web. Returns title, URL and a short snippet per result; read a result with web_fetch. Use it for "
        "current information, documentation and error messages.",
        schema(
            {"query": STRING, "count": {"type": "integer", "description": "Number of results, 1 to 20; default 8."}},
            ["query"],
        ),
        "auto",
        web_search,
        read_only=True,
        describe=lambda arguments: f"Search the web: {arguments.get('query', '')}",
        scope="always",
    ),
]
