from __future__ import annotations

import os
from typing import Any, Literal

import httpx

from openscour.errors import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    InvalidRequestError,
    NoResultsError,
)
from openscour.types import ExtractResponse, SearchResponse

DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_TIMEOUT = 60.0

SearchDepth = Literal["basic", "advanced"]
Topic = Literal["general", "news"]
TimeRange = Literal["day", "week", "month", "year"]


def _search_body(
    query: str,
    search_depth: SearchDepth,
    topic: Topic,
    max_results: int,
    include_answer: bool,
    include_raw_content: bool,
    include_domains: list[str] | None,
    exclude_domains: list[str] | None,
    time_range: TimeRange | None,
) -> dict[str, Any]:
    return {
        "query": query,
        "search_depth": search_depth,
        "topic": topic,
        "max_results": max_results,
        "include_answer": include_answer,
        "include_raw_content": include_raw_content,
        "include_domains": include_domains or [],
        "exclude_domains": exclude_domains or [],
        "time_range": time_range,
    }


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.is_success:
        return
    try:
        body = resp.json()
        detail = body.get("detail", body) if isinstance(body, dict) else body
    except ValueError:
        body = detail = resp.text
    message = detail if isinstance(detail, str) else str(detail)
    error = {401: AuthenticationError, 422: InvalidRequestError, 502: NoResultsError}.get(
        resp.status_code, APIStatusError
    )
    raise error(message, resp.status_code, body)


class _BaseClient:
    def __init__(self, api_key: str | None, base_url: str | None):
        self.api_key = api_key if api_key is not None else os.environ.get("OPENSCOUR_API_KEY")
        self.base_url = (base_url or os.environ.get("OPENSCOUR_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}


class OpenScour(_BaseClient):
    """Client for an OpenScour server.

    >>> client = OpenScour(api_key="...", base_url="http://localhost:8000")
    >>> response = client.search("Latest AWS Bedrock updates", max_results=10)
    >>> response.results[0].content

    ``api_key`` and ``base_url`` default to the ``OPENSCOUR_API_KEY`` and
    ``OPENSCOUR_BASE_URL`` environment variables.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        http_client: httpx.Client | None = None,
    ):
        super().__init__(api_key, base_url)
        self._client = http_client or httpx.Client(timeout=timeout)

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = self._client.post(f"{self.base_url}{path}", json=body, headers=self._headers())
        except httpx.HTTPError as e:
            raise APIConnectionError(f"Could not reach OpenScour at {self.base_url}: {e}") from e
        _raise_for_status(resp)
        return resp.json()

    def search(
        self,
        query: str,
        *,
        search_depth: SearchDepth = "basic",
        topic: Topic = "general",
        max_results: int = 5,
        include_answer: bool = False,
        include_raw_content: bool = False,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        time_range: TimeRange | None = None,
    ) -> SearchResponse:
        body = _search_body(
            query, search_depth, topic, max_results, include_answer,
            include_raw_content, include_domains, exclude_domains, time_range,
        )  # fmt: skip
        return SearchResponse.from_dict(self._post("/search", body))

    def extract(self, urls: str | list[str]) -> ExtractResponse:
        urls = [urls] if isinstance(urls, str) else urls
        return ExtractResponse.from_dict(self._post("/extract", {"urls": urls}))

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OpenScour:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class AsyncOpenScour(_BaseClient):
    """Async version of :class:`OpenScour`; same methods, awaited."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        http_client: httpx.AsyncClient | None = None,
    ):
        super().__init__(api_key, base_url)
        self._client = http_client or httpx.AsyncClient(timeout=timeout)

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = await self._client.post(f"{self.base_url}{path}", json=body, headers=self._headers())
        except httpx.HTTPError as e:
            raise APIConnectionError(f"Could not reach OpenScour at {self.base_url}: {e}") from e
        _raise_for_status(resp)
        return resp.json()

    async def search(
        self,
        query: str,
        *,
        search_depth: SearchDepth = "basic",
        topic: Topic = "general",
        max_results: int = 5,
        include_answer: bool = False,
        include_raw_content: bool = False,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        time_range: TimeRange | None = None,
    ) -> SearchResponse:
        body = _search_body(
            query, search_depth, topic, max_results, include_answer,
            include_raw_content, include_domains, exclude_domains, time_range,
        )  # fmt: skip
        return SearchResponse.from_dict(await self._post("/search", body))

    async def extract(self, urls: str | list[str]) -> ExtractResponse:
        urls = [urls] if isinstance(urls, str) else urls
        return ExtractResponse.from_dict(await self._post("/extract", {"urls": urls}))

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> AsyncOpenScour:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()
