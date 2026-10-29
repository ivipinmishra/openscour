from __future__ import annotations

import asyncio
import os
import random
import time
from typing import Any, Literal

import httpx

from openscour.errors import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    InvalidRequestError,
    NoResultsError,
    RateLimitError,
)
from openscour.types import ExtractResponse, SearchResponse

DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_RETRIES = 2
# Longest wait between retries; a longer Retry-After is raised to the caller instead.
MAX_RETRY_WAIT = 30.0

# Connection failures that happen before the server could act on the request.
# Read timeouts are not retried: the search may still be running.
_RETRYABLE_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError)

# Module-level so tests can skip the waiting.
_sleep = time.sleep
_async_sleep = asyncio.sleep

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


def _extract_body(urls: str | list[str], query: str | None, chunks_per_source: int) -> dict[str, Any]:
    body: dict[str, Any] = {"urls": [urls] if isinstance(urls, str) else urls}
    if query:
        body["query"] = query
        body["chunks_per_source"] = chunks_per_source
    return body


def _is_json(resp: httpx.Response) -> bool:
    return resp.headers.get("content-type", "").startswith("application/json")


def _retry_after(resp: httpx.Response) -> float | None:
    try:
        return max(0.0, float(resp.headers["retry-after"]))
    except (KeyError, ValueError):
        return None


def _retry_wait(attempt: int, resp: httpx.Response | None = None) -> float | None:
    """Seconds to wait before retrying, or None if this outcome should not be retried."""
    if resp is not None:
        # OpenScour's own 502 means no search results, which a retry won't change;
        # a 502 page from a proxy in front of it is transient.
        transient = resp.status_code in (429, 503, 504) or (resp.status_code == 502 and not _is_json(resp))
        if not transient:
            return None
        if (wait := _retry_after(resp)) is not None:
            return wait if wait <= MAX_RETRY_WAIT else None
    # Exponential backoff with jitter, so many clients don't retry in step.
    return min(MAX_RETRY_WAIT, 0.5 * 2**attempt) * random.uniform(0.5, 1.0)


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.is_success:
        return
    try:
        body = resp.json()
        detail = body.get("detail", body) if isinstance(body, dict) else body
    except ValueError:
        body = detail = resp.text
    message = detail if isinstance(detail, str) else str(detail)
    if resp.status_code == 429:
        raise RateLimitError(message, 429, body, retry_after=_retry_after(resp))
    if resp.status_code == 502 and not _is_json(resp):
        raise APIStatusError(message, 502, body)
    error = {401: AuthenticationError, 422: InvalidRequestError, 502: NoResultsError}.get(
        resp.status_code, APIStatusError
    )
    raise error(message, resp.status_code, body)


class _BaseClient:
    def __init__(self, api_key: str | None, base_url: str | None, max_retries: int):
        self.max_retries = max_retries
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
    ``OPENSCOUR_BASE_URL`` environment variables. Requests that fail to connect
    or get 429, 503 or 504 are retried up to ``max_retries`` times, honoring the
    server's Retry-After.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        http_client: httpx.Client | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ):
        super().__init__(api_key, base_url, max_retries)
        self._client = http_client or httpx.Client(timeout=timeout)

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        attempt = 0
        while True:
            try:
                resp = self._client.post(f"{self.base_url}{path}", json=body, headers=self._headers())
            except httpx.HTTPError as e:
                if isinstance(e, _RETRYABLE_ERRORS) and attempt < self.max_retries:
                    _sleep(_retry_wait(attempt))
                    attempt += 1
                    continue
                raise APIConnectionError(f"Could not reach OpenScour at {self.base_url}: {e}") from e
            if attempt < self.max_retries and (wait := _retry_wait(attempt, resp)) is not None:
                _sleep(wait)
                attempt += 1
                continue
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

    def extract(
        self, urls: str | list[str], *, query: str | None = None, chunks_per_source: int = 3
    ) -> ExtractResponse:
        """Clean text of each page; with ``query``, only its most relevant passages."""
        return ExtractResponse.from_dict(self._post("/extract", _extract_body(urls, query, chunks_per_source)))

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
        max_retries: int = DEFAULT_MAX_RETRIES,
    ):
        super().__init__(api_key, base_url, max_retries)
        self._client = http_client or httpx.AsyncClient(timeout=timeout)

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        attempt = 0
        while True:
            try:
                resp = await self._client.post(f"{self.base_url}{path}", json=body, headers=self._headers())
            except httpx.HTTPError as e:
                if isinstance(e, _RETRYABLE_ERRORS) and attempt < self.max_retries:
                    await _async_sleep(_retry_wait(attempt))
                    attempt += 1
                    continue
                raise APIConnectionError(f"Could not reach OpenScour at {self.base_url}: {e}") from e
            if attempt < self.max_retries and (wait := _retry_wait(attempt, resp)) is not None:
                await _async_sleep(wait)
                attempt += 1
                continue
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

    async def extract(
        self, urls: str | list[str], *, query: str | None = None, chunks_per_source: int = 3
    ) -> ExtractResponse:
        """Clean text of each page; with ``query``, only its most relevant passages."""
        return ExtractResponse.from_dict(await self._post("/extract", _extract_body(urls, query, chunks_per_source)))

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> AsyncOpenScour:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()
