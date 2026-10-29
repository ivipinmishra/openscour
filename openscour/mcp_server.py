"""MCP server that gives AI agents OpenScour's search and extract as tools.

Runs over stdio and talks to an OpenScour server through the Python client,
configured with OPENSCOUR_BASE_URL and OPENSCOUR_API_KEY.

    claude mcp add openscour -- openscour-mcp
"""

from __future__ import annotations

from typing import Literal

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from openscour.client import AsyncOpenScour
from openscour.errors import APIConnectionError, OpenScourError
from openscour.types import ExtractResponse, SearchResponse

INSTRUCTIONS = (
    "OpenScour searches the web and returns ranked passages from real pages. "
    "Use `search` for current events, recent releases, facts you are unsure of, "
    "or anything after your training data. Use `extract` to read the full text "
    "of specific URLs. Cite the URLs of the passages you rely on."
)

# Upper bound on extracted page text per URL, so one long page can't flood the context.
MAX_EXTRACT_CHARS = 20_000

server = MCPServer(name="openscour", instructions=INSTRUCTIONS)

_READ_ONLY_WEB = ToolAnnotations(readOnlyHint=True, openWorldHint=True)


@asynccontextmanager
async def _client() -> AsyncIterator[AsyncOpenScour]:
    """An OpenScour client whose errors reach the agent as readable tool errors.

    Other exceptions only show the agent "Error executing tool", which gives it
    nothing to act on (e.g. that the OpenScour server isn't running).
    """
    try:
        async with AsyncOpenScour() as client:
            yield client
    except APIConnectionError as e:
        raise ToolError(f"{e}. Is the OpenScour server running?") from e
    except OpenScourError as e:
        raise ToolError(str(e)) from e


def format_search(response: SearchResponse) -> str:
    if not response.results:
        return f"No results for {response.query!r}."
    parts = []
    if response.answer:
        parts.append(f"Answer: {response.answer}")
    for i, r in enumerate(response.results, start=1):
        lines = [f"[{i}] {r.title}", f"URL: {r.url}"]
        if r.published_date:
            lines.append(f"Published: {r.published_date}")
        lines.append(r.content)
        if r.raw_content:
            lines.append(f"Full text:\n{r.raw_content[:MAX_EXTRACT_CHARS]}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def format_extract(response: ExtractResponse) -> str:
    parts = []
    for r in response.results:
        text = r.raw_content
        if len(text) > MAX_EXTRACT_CHARS:
            text = text[:MAX_EXTRACT_CHARS] + f"\n[... truncated, {len(r.raw_content)} characters in total]"
        parts.append(f"URL: {r.url}\n{text}")
    for f in response.failed_results:
        parts.append(f"URL: {f.url}\nCould not extract: {f.error}")
    return "\n\n---\n\n".join(parts) or "No URLs were extracted."


@server.tool(
    description=(
        "Search the web and get the most relevant passage from each result page. "
        "Use for current or factual information. Results are numbered with title, URL and passage."
    ),
    annotations=_READ_ONLY_WEB,
)
async def search(
    query: str,
    max_results: int = 5,
    search_depth: Literal["basic", "advanced"] = "basic",
    topic: Literal["general", "news"] = "general",
    time_range: Literal["day", "week", "month", "year"] | None = None,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
) -> str:
    """
    Args:
        query: What to search for, e.g. "Latest AWS Bedrock updates".
        max_results: Number of results, 1 to 20.
        search_depth: "advanced" reads more pages and returns more passages per result; slower.
        topic: "news" for recent news coverage.
        time_range: Only results from the last day, week, month or year.
        include_domains: Only return results from these domains.
        exclude_domains: Never return results from these domains.
    """
    async with _client() as client:
        response = await client.search(
            query,
            max_results=max_results,
            search_depth=search_depth,
            topic=topic,
            time_range=time_range,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
        )
    return format_search(response)


@server.tool(
    description="Get the clean main text of one or more web pages, without navigation or ads.",
    annotations=_READ_ONLY_WEB,
)
async def extract(urls: list[str]) -> str:
    """
    Args:
        urls: Up to 20 http(s) URLs to read.
    """
    async with _client() as client:
        response = await client.extract(urls)
    return format_extract(response)


def main() -> None:
    """Entry point for the ``openscour-mcp`` command."""
    server.run("stdio")


if __name__ == "__main__":
    main()
