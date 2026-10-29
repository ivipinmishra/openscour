"""robots.txt checks, so OpenScour only reads pages sites allow automated access to."""

from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

log = logging.getLogger(__name__)

# The product token sites can name in robots.txt ("User-agent: OpenScour").
ROBOTS_AGENT = "OpenScour"
ROBOTS_TIMEOUT = 2.0
MAX_ROBOTS_BYTES = 512_000


class RobotsChecker:
    """Fetches and caches each site's robots.txt and answers whether a URL may be read.

    Following RFC 9309, a missing robots.txt (4xx) allows everything. When
    robots.txt can't be fetched at all (timeout, 5xx), the page is also allowed:
    a slow robots.txt shouldn't hide a site from search, and page fetches keep
    their own timeouts.
    """

    def __init__(self, client: httpx.AsyncClient, ttl_seconds: float = 3600) -> None:
        self._client = client
        self._ttl = ttl_seconds
        self._rules: dict[str, tuple[float, RobotFileParser | None]] = {}
        self._inflight: dict[str, asyncio.Task[RobotFileParser | None]] = {}

    async def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        rules = await self._rules_for(origin)
        return rules is None or rules.can_fetch(ROBOTS_AGENT, url)

    async def _rules_for(self, origin: str) -> RobotFileParser | None:
        cached = self._rules.get(origin)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        # One robots.txt download per site, however many pages are requested at once.
        task = self._inflight.get(origin)
        if task is None:
            task = asyncio.create_task(self._download(origin))
            self._inflight[origin] = task
            task.add_done_callback(lambda _t: self._inflight.pop(origin, None))
        rules = await asyncio.shield(task)
        self._rules[origin] = (time.monotonic() + self._ttl, rules)
        return rules

    async def _download(self, origin: str) -> RobotFileParser | None:
        try:
            resp = await self._client.get(f"{origin}/robots.txt", timeout=ROBOTS_TIMEOUT, follow_redirects=True)
        except httpx.HTTPError as e:
            log.debug("robots.txt unavailable for %s: %s", origin, e)
            return None
        if resp.status_code != 200:
            return None
        parser = RobotFileParser()
        parser.parse(resp.text[:MAX_ROBOTS_BYTES].splitlines())
        return parser
