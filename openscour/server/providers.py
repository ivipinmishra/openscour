import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from openscour.server.config import settings

log = logging.getLogger(__name__)


@dataclass
class RawResult:
    title: str
    url: str
    snippet: str
    published_date: str | None = None


class ProviderError(Exception):
    pass


class EngineHealth:
    """What each SearXNG engine did in recent searches: "ok", or why it failed.

    Learned from real searches only. Probing the engines separately would
    send them more automated traffic, which is what gets them blocked.
    """

    def __init__(self) -> None:
        self._status: dict[str, tuple[str, float]] = {}

    def record(self, answered: list[str], failed: dict[str, str]) -> None:
        now = time.monotonic()
        for name in answered:
            self._status[name] = ("ok", now)
        for name, reason in failed.items():
            self._status[name] = (reason, now)

    def recent(self, max_age_seconds: float = 900) -> dict[str, str]:
        cutoff = time.monotonic() - max_age_seconds
        return {name: status for name, (status, at) in sorted(self._status.items()) if at >= cutoff}


engine_health = EngineHealth()


def normalize_url(url: str) -> str:
    parts = urlsplit(url)
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme, parts.netloc.lower().removeprefix("www."), path, parts.query, ""))


def domain_of(url: str) -> str:
    return urlsplit(url).netloc.lower().removeprefix("www.")


def _matches(domain: str, patterns: list[str]) -> bool:
    return any(domain == p or domain.endswith("." + p) for p in patterns)


def filter_results(
    results: list[RawResult], include_domains: list[str], exclude_domains: list[str]
) -> list[RawResult]:
    include = [d.lower().removeprefix("www.") for d in include_domains]
    exclude = [d.lower().removeprefix("www.") for d in exclude_domains]
    seen: set[str] = set()
    out = []
    for r in results:
        if not r.url.startswith(("http://", "https://")):
            continue
        key = normalize_url(r.url)
        if key in seen:
            continue
        domain = domain_of(r.url)
        if include and not _matches(domain, include):
            continue
        if exclude and _matches(domain, exclude):
            continue
        seen.add(key)
        out.append(r)
    return out


async def searxng_search(
    client: httpx.AsyncClient, query: str, topic: str, time_range: str | None, stats: dict[str, Any]
) -> list[RawResult]:
    params = {
        "q": query,
        "format": "json",
        "categories": "news" if topic == "news" else "general",
    }
    if time_range:
        params["time_range"] = time_range
    try:
        resp = await client.get(f"{settings.searxng_url.rstrip('/')}/search", params=params, timeout=12)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        raise ProviderError(f"SearXNG request failed: {e}") from e

    results = data.get("results", [])
    answered = sorted({e for r in results for e in r.get("engines", [])})
    # A paused engine reports "Suspended: <reason>"; the reason is what matters.
    failed = {name: reason.removeprefix("Suspended: ") for name, reason in data.get("unresponsive_engines", [])}
    engine_health.record(answered, failed)
    stats["engines"] = answered
    stats["engines_failed"] = failed

    return [
        RawResult(
            title=r.get("title") or "",
            url=r.get("url") or "",
            snippet=r.get("content") or "",
            published_date=r.get("publishedDate"),
        )
        for r in results
    ]


_DDG_TIMELIMIT = {"day": "d", "week": "w", "month": "m", "year": "y"}


def _ddg_search_sync(query: str, max_results: int, topic: str, time_range: str | None) -> list[RawResult]:
    from ddgs import DDGS

    timelimit = _DDG_TIMELIMIT.get(time_range) if time_range else None
    with DDGS() as ddgs:
        if topic == "news":
            rows = ddgs.news(query, max_results=max_results, timelimit=timelimit)
            return [RawResult(r.get("title", ""), r.get("url", ""), r.get("body", ""), r.get("date")) for r in rows]
        rows = ddgs.text(query, max_results=max_results, timelimit=timelimit)
        return [RawResult(r.get("title", ""), r.get("href", ""), r.get("body", "")) for r in rows]


async def search(
    client: httpx.AsyncClient,
    query: str,
    max_candidates: int,
    topic: str = "general",
    time_range: str | None = None,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    stats: dict[str, Any] | None = None,
) -> list[RawResult]:
    """Search results, best first. ``stats``, if given, is filled with which engines answered."""
    stats = {} if stats is None else stats
    include_domains = include_domains or []
    exclude_domains = exclude_domains or []
    # A single site: filter is understood by most engines; more than one is
    # handled by post-filtering only.
    q = f"{query} site:{include_domains[0]}" if len(include_domains) == 1 else query

    results: list[RawResult] = []
    try:
        results = await searxng_search(client, q, topic, time_range, stats)
    except ProviderError as e:
        log.warning("%s", e)

    results = filter_results(results, include_domains, exclude_domains)
    if not results and settings.duckduckgo_fallback:
        log.info("Falling back to DuckDuckGo for %r", query)
        stats["fallback"] = "duckduckgo"
        try:
            ddg = await asyncio.to_thread(_ddg_search_sync, q, max_candidates * 2, topic, time_range)
            results = filter_results(ddg, include_domains, exclude_domains)
        except Exception as e:  # ddgs raises its own exception types
            log.warning("DuckDuckGo fallback failed: %s", e)

    return results[:max_candidates]
