"""robots.txt checks, so OpenScour only reads pages sites allow automated access to."""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from openscour.server.cache import TTLCache

log = logging.getLogger(__name__)

# The product token sites can name in robots.txt ("User-agent: OpenScour").
ROBOTS_AGENT = "OpenScour"
ROBOTS_TIMEOUT = 1.0
MAX_ROBOTS_BYTES = 512_000


class RobotsChecker:
    """Fetches and caches each site's robots.txt and answers whether a URL may be read.

    Following RFC 9309, a missing robots.txt (4xx) allows everything. When
    robots.txt can't be fetched at all (timeout, 5xx), the page is also allowed:
    a slow robots.txt shouldn't hide a site from search, and page fetches keep
    their own timeouts.
    """

    def __init__(self, client: httpx.AsyncClient, ttl_seconds: int = 3600, max_items: int = 4096) -> None:
        self._client = client
        self._rules = TTLCache(ttl_seconds=ttl_seconds, max_items=max_items)
        self._inflight: dict[str, asyncio.Task[RobotFileParser | None]] = {}

    async def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        rules = await self._rules_for(origin)
        return rules.can_fetch(ROBOTS_AGENT, url)

    async def _rules_for(self, origin: str) -> RobotFileParser:
        cached = self._rules.get(origin)
        if cached is not None:
            return cached
        # One robots.txt download per site, however many pages are requested at once.
        task = self._inflight.get(origin)
        if task is None:
            task = asyncio.create_task(self._download(origin))
            self._inflight[origin] = task
            task.add_done_callback(lambda _t: self._inflight.pop(origin, None))
        rules = await asyncio.shield(task)
        if rules is None:
            rules = RobotFileParser()
            rules.allow_all = True
        self._rules.set(origin, rules)
        return rules

    async def _download(self, origin: str) -> RobotFileParser | None:
        try:
            async with self._client.stream(
                "GET", f"{origin}/robots.txt", timeout=ROBOTS_TIMEOUT, follow_redirects=True
            ) as resp:
                if resp.status_code != 200:
                    return None
                body = bytearray()
                async for chunk in resp.aiter_bytes():
                    body.extend(chunk)
                    if len(body) >= MAX_ROBOTS_BYTES:
                        break
                text = body[:MAX_ROBOTS_BYTES].decode(resp.encoding or "utf-8", errors="replace")
        except httpx.HTTPError as e:
            log.debug("robots.txt unavailable for %s: %s", origin, e)
            return None
        parser = RobotFileParser()
        parser.parse(text.splitlines())
        return parser
