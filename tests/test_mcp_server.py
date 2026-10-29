from mcp import Client

from openscour.mcp_server import MAX_EXTRACT_CHARS, _shared_client, format_extract, format_search, server
from openscour.types import ExtractResponse, ExtractResult, FailedResult, SearchResponse, SearchResult


async def test_tools_are_registered_as_read_only():
    async with Client(server) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == {"search", "extract"}
    assert "query" in tools["search"].input_schema["required"]
    assert all(t.annotations.read_only_hint for t in tools.values())


def test_format_search_numbers_results_with_urls():
    response = SearchResponse(
        query="Who is vice president in the USA?",
        response_time=1.0,
        answer="JD Vance [1]",
        results=[
            SearchResult(
                title="Vice President of the United States",
                url="https://en.wikipedia.org/wiki/Vice_President_of_the_United_States",
                content="JD Vance is the 50th and current vice president.",
                score=1.0,
                published_date="2026-10-01",
            )
        ],
    )
    text = format_search(response)
    assert text.startswith("Answer: JD Vance [1]")
    assert "[1] Vice President of the United States" in text
    assert "URL: https://en.wikipedia.org/wiki/Vice_President_of_the_United_States" in text
    assert "Published: 2026-10-01" in text


def test_format_search_empty():
    assert format_search(SearchResponse(query="zzz", results=[], response_time=0.1)) == "No results for 'zzz'."


def test_format_extract_truncates_and_reports_failures():
    long_text = "x" * (MAX_EXTRACT_CHARS + 500)
    response = ExtractResponse(
        results=[ExtractResult(url="https://a.com", raw_content=long_text)],
        failed_results=[FailedResult(url="https://bad.invalid", error="ConnectError")],
    )
    text = format_extract(response)
    assert f"truncated, {len(long_text)} characters in total" in text
    assert "URL: https://bad.invalid\nCould not extract: ConnectError" in text
    assert len(text) < MAX_EXTRACT_CHARS + 300


async def test_extract_tool_offers_query():
    async with Client(server) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    props = tools["extract"].input_schema["properties"]
    assert {"urls", "query", "chunks_per_source"} <= set(props)
    assert tools["extract"].input_schema["required"] == ["urls"]


async def test_tool_calls_share_one_client():
    assert _shared_client() is _shared_client()
