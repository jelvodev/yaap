"""Web search for the agent through SearXNG.

SearXNG is a self-hosted meta search engine. It runs as its own service next to YAAP (docker-compose.yml), needs no API
key and works for every provider and connection, unlike the providers' own server-side search tools. YAAP reaches it
at `YAAP_SEARCH_URL`; without that address the agent has no web_search tool.
"""

import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Protocol

MAX_RESULTS = 20
TAG = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class WebResult:
    title: str
    url: str
    snippet: str


class SearchError(Exception):
    """The search failed; the message is safe to show to the model and the user."""


class SearchProvider(Protocol):
    name: str

    def search(self, query: str, count: int) -> list[WebResult]: ...


def plain(text: str) -> str:
    """Some engines mark matched words with HTML; the result is plain text."""
    return " ".join(html.unescape(TAG.sub("", text or "")).split())


class SearxngSearch:
    name = "SearXNG"

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def request(self, query: str) -> dict:
        url = f"{self.base_url}/search?{urllib.parse.urlencode({'q': query, 'format': 'json'})}"
        request = urllib.request.Request(
            url,
            # SearXNG's bot detection wants the client address from a proxy header; YAAP is the only client.
            headers={"Accept": "application/json", "User-Agent": "YAAP", "X-Real-IP": "127.0.0.1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code == 403:
                raise SearchError("The search service refuses JSON searches; its settings must allow the json format.") from error
            if error.code == 429:
                raise SearchError("The search service limits the number of searches; try again later.") from error
            raise SearchError(f"The search service returned an error (HTTP {error.code}).") from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise SearchError("Could not reach the search service (SearXNG). Is it running?") from error
        except ValueError as error:
            raise SearchError("The search service returned something that is not JSON.") from error

    def search(self, query: str, count: int) -> list[WebResult]:
        body = self.request(query)
        results = [item for item in body.get("results") or [] if item.get("url")]
        return [
            WebResult(plain(item.get("title", "")), str(item["url"]), plain(item.get("content", "")))
            for item in results[: max(1, min(count, MAX_RESULTS))]
        ]


def format_results(query: str, results: list[WebResult]) -> str:
    if not results:
        return f"No web results for: {query}"
    lines = [f"Web results for: {query}", ""]
    for number, result in enumerate(results, 1):
        lines.append(f"{number}. {result.title}\n   {result.url}\n   {result.snippet}".rstrip())
    lines.append("")
    lines.append("Read a page with web_fetch when a snippet is not enough.")
    return "\n".join(lines)
